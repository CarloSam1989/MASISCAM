"""Disposable static-only Docker regression. No .env, database, Drive or private mounts.

Run: python deploy/test_staticfiles_docker.py
"""
import os
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def configure():
    from django.conf import settings
    root = Path(os.environ.get('MASISCAM_STATIC_QA_ROOT', '/app/staticfiles'))
    app = Path(os.environ.get('MASISCAM_STATIC_QA_APP', '/app'))
    settings.configure(
        BASE_DIR=root.parent, SECRET_KEY='static-only-disposable-qa',
        INSTALLED_APPS=['django.contrib.staticfiles'], STATIC_URL='/static/',
        STATIC_ROOT=root, STATICFILES_DIRS=[app / 'static', app / 'masiscam' / 'static'],
        MEDIA_ROOT=root.parent / 'media', FILE_UPLOAD_PERMISSIONS=0o640,
        FILE_UPLOAD_DIRECTORY_PERMISSIONS=0o750,
        STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                  'staticfiles': {'BACKEND': 'config.storage.PublicStaticFilesStorage'}},
    )
    import django
    django.setup()
    return root


def public_modes(root):
    for path in [root, *root.rglob('*')]:
        expected = 0o755 if path.is_dir() else 0o644
        assert stat.S_IMODE(path.stat().st_mode) == expected, str(path)


def collect(serve=False):
    from django.core.management import call_command
    root = configure()
    os.umask(0o077)  # Reproduce the restrictive publishing environment.
    secret_dir = root.parent / 'qa-secrets'
    private_dir = root.parent / 'media'
    if serve:
        assert os.getuid() == 10001
        for directory in (secret_dir, private_dir):
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
            (directory / 'private-qa').write_bytes(b'PRIVATE QA ONLY')
            (directory / 'private-qa').chmod(0o600)
    protected = [secret_dir, secret_dir / 'private-qa', private_dir, private_dir / 'private-qa']
    before = [path.stat().st_mode for path in protected]
    call_command('collectstatic', interactive=False, verbosity=0)
    public_modes(root)
    if serve:
        target = root / 'masiscam/css/taller.css'
        modified = target.stat().st_mtime_ns
        target.chmod(0o600)
        target.parent.chmod(0o700)
        root.chmod(0o700)
        call_command('collectstatic', interactive=False, verbosity=0)
        assert target.stat().st_mtime_ns == modified  # unchanged file, no recopy
        public_modes(root)
    assert before == [path.stat().st_mode for path in protected]
    print('STATIC_MODES_OK: public 644/755; private modes unchanged', flush=True)
    if serve:
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        (Path('/tmp') / 'static-qa-ready').touch()
        ThreadingHTTPServer(('0.0.0.0', 8000), SimpleHTTPRequestHandler).serve_forever()


def http_check(denied=False):
    # Wait for the UID-10001 collector to finish before probing Nginx.
    for attempt in range(40):
        try:
            with urlopen('http://web:8000/', timeout=2):
                break
        except (OSError, URLError):
            if attempt == 39:
                raise
            time.sleep(0.5)
    samples = {
        'masiscam/css/taller.css': {'text/css'},
        'vendor/bootstrap.bundle.min.js': {'application/javascript', 'text/javascript'},
        'masiscam/img/logo_transparente.png': {'image/png'},
    }
    if denied:
        try:
            urlopen('http://nginx/static/masiscam/css/taller.css', timeout=5)
        except HTTPError as error:
            assert error.code == 403
            assert error.headers.get_content_type() == 'text/html'
            print('REPRODUCED: 0600 CSS returns 403 HTML', flush=True)
            return
        raise AssertionError('Expected 403: Nginx must use a different UID')
    for name, content_types in samples.items():
        with urlopen('http://nginx/static/' + name, timeout=5) as response:
            assert response.status == 200
            assert response.headers.get_content_type() in content_types, name
            assert response.read(), name
            print(f'HTTP 200 {response.headers.get_content_type()} /static/{name}', flush=True)
    for path in ('/static/missing-qa.css', '/media/private-qa'):
        try:
            urlopen('http://nginx' + path, timeout=5)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError('Expected 404: ' + path)


def docker_test():
    import json
    from tempfile import TemporaryDirectory
    app = Path(__file__).resolve().parents[1]
    script = Path(__file__).resolve()
    config = app / 'deploy/nginx/default.conf'
    name = 'masiscam-static-qa-' + uuid.uuid4().hex[:10]
    image, volume, network = name + ':qa', name + '-assets', name + '-net'
    collector, nginx = name + '-collector', name + '-nginx'

    def docker(*args, timeout=120):
        return subprocess.run(['docker', *map(str, args)], check=True, timeout=timeout)

    # Render the actual deployment topology without reading a real .env file.
    with TemporaryDirectory(prefix='masiscam-static-compose-qa-') as temp:
        env = Path(temp) / 'qa.env'
        env.write_text('POSTGRES_PASSWORD=disposable-static-qa\n', encoding='utf-8')
        config_json = subprocess.check_output([
            'docker', 'compose', '--env-file', str(env), '-f', str(app / 'docker-compose.yml'),
            'config', '--no-env-resolution', '--format', 'json'], timeout=30)
        services = json.loads(config_json)['services']
        writer = next(v for v in services['init']['volumes'] if v['target'] == '/app/staticfiles')
        reader = next(v for v in services['nginx']['volumes'] if v['target'] == '/static')
        assert writer['source'] == reader['source']
        assert writer['type'] == reader['type'] == 'volume'
        assert not writer.get('read_only', False) and reader['read_only'] is True
        print('COMPOSE_OK: collectstatic and read-only Nginx share the same volume', flush=True)
    # Fail before creating Docker resources if the local engine is unavailable.
    docker('info', '--format', '{{.ServerVersion}}', timeout=20)
    try:
        docker('build', '-t', image, app, timeout=900)
        docker('volume', 'create', '--label', 'masiscam.qa=staticfiles', volume)
        docker('network', 'create', '--label', 'masiscam.qa=staticfiles', network)
        docker('run', '-d', '--name', collector, '--network', network, '--network-alias', 'web',
               '--mount', f'type=volume,source={volume},target=/app/staticfiles',
               '--mount', f'type=bind,source={script},target=/qa/staticfiles.py,readonly',
               image, 'python', '/qa/staticfiles.py', 'collect')
        docker('run', '-d', '--name', nginx, '--network', network, '--network-alias', 'nginx',
               '--mount', f'type=volume,source={volume},target=/static,readonly',
               '--mount', f'type=bind,source={config},target=/etc/nginx/conf.d/default.conf,readonly',
               'nginx:1.28-alpine')
        mounts = json.loads(subprocess.check_output(['docker', 'inspect', nginx]))[0]['Mounts']
        assert next(m for m in mounts if m['Destination'] == '/static')['RW'] is False
        docker('exec', collector, 'python', '/qa/staticfiles.py', 'http')
        docker('exec', collector, 'chmod', '600', '/app/staticfiles/masiscam/css/taller.css')
        docker('exec', collector, 'python', '/qa/staticfiles.py', 'denied')
        docker('exec', collector, 'python', '/qa/staticfiles.py', 'repair')
        docker('exec', collector, 'python', '/qa/staticfiles.py', 'http')
        print('DOCKER STATIC REGRESSION PASSED', flush=True)
    finally:
        for container in (nginx, collector):
            subprocess.run(['docker', 'logs', '--tail', '15', container], check=False)
            subprocess.run(['docker', 'rm', '-f', container], check=False, capture_output=True)
        subprocess.run(['docker', 'network', 'rm', network], check=False, capture_output=True)
        subprocess.run(['docker', 'volume', 'rm', volume], check=False, capture_output=True)
        subprocess.run(['docker', 'image', 'rm', image], check=False, capture_output=True)


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'docker'
    if mode == 'collect':
        collect(serve=True)
    elif mode == 'repair':
        collect()
    elif mode in {'http', 'denied'}:
        http_check(denied=mode == 'denied')
    elif mode == 'unit':
        configure()
        from django.test.runner import DiscoverRunner
        sys.exit(bool(DiscoverRunner(verbosity=2).run_tests(['config.test_staticfiles'])))
    elif mode == 'docker':
        docker_test()
    else:
        raise SystemExit('Unknown QA mode')
