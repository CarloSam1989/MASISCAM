"""Responsive operational screens, with isolated PostgreSQL and no real Drive."""
import os
import re
import uuid
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from . import test_taller, test_tecnica as qa
from .models import AsignacionTecnica, ConfirmacionRecepcion, SolicitudRepuestos
from .recepcion_services import abrir_recepcion
from .recepcion_confirmacion import confirmar_recepcion
from .tecnica_models import Diagnostico, Inspeccion
from .test_tres_roles import TresRolesFixture


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1','Activar navegador QA explícitamente')
@override_settings(GOOGLE_DRIVE_ENABLED=False,MASISCAM_RECEPCION_LOCAL=True,MASISCAM_TECNICA_LOCAL=True,
    MASISCAM_OPERACION_LOCAL=True,MASISCAM_CALIBRACION_LOCAL=True,MASISCAM_CIERRE_LOCAL=True)
class ExperienciaBrowserTests(StaticLiveServerTestCase):
    datos=TresRolesFixture.datos
    crear=TresRolesFixture.crear
    recepcion=TresRolesFixture.recepcion

    def setUp(self):
        super().setUp()
        TresRolesFixture.setUp(self)

    def test_recepcion_y_navegacion_en_escritorio_tablet_movil(self):
        from playwright.sync_api import sync_playwright,expect
        nuevo=self.crear('UX-NUEVO')
        equipo=self.crear('UX-SIN-FOTOS')
        exp=abrir_recepcion(usuario=self.taller,empresa=self.empresa,equipo_id=equipo.pk,
            cliente_id=self.cliente.pk,clave=uuid.uuid4(),tipo='MANTENIMIENTO',fecha=timezone.now(),
            condicion_ingreso='Equipo ficticio para validar interfaz',fotografias=[])
        otro=self.crear('UX-SIN-ASIGNAR')
        sin_asignar=self.recepcion(otro)
        confirmar_recepcion(usuario=self.taller,empresa=self.empresa,expediente_id=sin_asignar.pk,
            clave=uuid.uuid4(),placa='SIN_PLACA',motivo_sin_placa='QA',motivo_sin_guia='QA',
            general_id=sin_asignar.recepcion.evidencias.get().pk)
        AsignacionTecnica.objects.filter(expediente=sin_asignar).update(fin=timezone.now())
        from PIL import Image
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        def imagen(color):
            stream=BytesIO(); Image.new('RGB',(40,30),color).save(stream,'PNG')
            return stream.getvalue()
        plano_eq=self.crear('UX-SIN-PLANO')
        plano_exp=abrir_recepcion(usuario=self.taller,empresa=self.empresa,equipo_id=plano_eq.pk,
            cliente_id=self.cliente.pk,clave=uuid.uuid4(),tipo='MANTENIMIENTO',fecha=timezone.now(),
            condicion_ingreso='QA',fotografias=[SimpleUploadedFile('general-verde.png',imagen('green'),content_type='image/png')])
        confirmar_recepcion(usuario=self.taller,empresa=self.empresa,expediente_id=plano_exp.pk,
            clave=uuid.uuid4(),placa='SIN_PLACA',motivo_sin_placa='QA',motivo_sin_guia='QA',
            general_id=plano_exp.recepcion.evidencias.get().pk)
        qa.inspeccion_enviada(self.empresa,self.taller,plano_exp)
        qa.s.crear_revision(usuario=self.taller,empresa=self.empresa,expediente_id=plano_exp.pk,
            tipo='DIAGNOSTICO',clave=uuid.uuid4())
        cookies={}
        for role in ('TALLER','OFICINA','PLANTA','GERENCIA'):
            client=Client();client.force_login(self.usuarios[role])
            cookies[role]=client.cookies[settings.SESSION_COOKIE_NAME].value
        foto=imagen('yellow')
        output=Path(os.environ.get('MASISCAM_UI_EVIDENCE_DIR',settings.BASE_DIR/'.venv-rbac/experiencia-evidencias'))
        output.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for role in cookies:
                    context=browser.new_context(viewport={'width':1440,'height':1000})
                    context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookies[role],url=self.live_server_url)])
                    page=context.new_page()
                    page.add_locator_handler(page.locator('.swal2-confirm:visible:enabled'),lambda button:button.click(),no_wait_after=True)
                    for width in (1440,768,390):
                        page.set_viewport_size({'width':width,'height':1000})
                        url=self.live_server_url+reverse('masiscam:expediente_detalle',args=[exp.pk])
                        self.assertEqual(page.goto(url).status,200)
                        expect(page.get_by_role('navigation',name='Migas de pan')).to_be_visible()
                        if width==390:
                            ancestors=page.locator('.masiscam-breadcrumbs')  # las migas actuales son una lista simple, sin desplegable
                            parent='Mis equipos' if role=='TALLER' else 'Solicitudes' if role=='OFICINA' else 'Expedientes'
                            expect(ancestors.get_by_role('link',name=parent,exact=True)).to_be_visible()
                            expect(page.locator('.masiscam-breadcrumbs [aria-current=page]')).to_have_text('Recepción')
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                        self.assertEqual(page.locator('.mf-main-action:visible').count(),1,f"{role} {width}: "+str(page.locator('.mf-main-action:visible').evaluate_all('e=>e.map(x=>x.outerHTML.slice(0,160))')))
                        if role=='TALLER':
                            expect(page.get_by_text('Para confirmar la recepción, adjunta una fotografía general del equipo',exact=True)).to_be_visible()
                            self.assertEqual(page.get_by_role('button',name='Reintentar generación / sincronización').count(),0)
                            page.screenshot(path=str(output/f'01-recepcion-sin-fotos-{width}.png'),full_page=True)
                        else:
                            page.screenshot(path=str(output/f'recepcion-{role.lower()}-{width}.png'),full_page=True)
                    if role=='TALLER':
                        page.locator('#id_foto_general').set_input_files(dict(name='general-qa.png',mimeType='image/png',buffer=foto))
                        page.locator('#id_placa').select_option('SIN_PLACA')
                        page.locator('#id_motivo_sin_placa').fill('Equipo ficticio sin placa, evidencia QA')
                        page.locator('#id_motivo_sin_guia').fill('Ingreso ficticio sin remisión')
                        page.get_by_role('button',name='Confirmar recepción',exact=True).click()
                        page.wait_for_url('**/taller/equipos/**')
                        expect(page.get_by_role('button',name='Iniciar inspección')).to_be_visible()
                        for width in (1440,768,390):
                            page.set_viewport_size({'width':width,'height':1000})
                            for nombre,url,texto in [
                                ('00-equipo-nuevo',reverse('masiscam:taller_equipo',args=[nuevo.pk]),'Iniciar mantenimiento'),
                                ('02-recepcion-confirmada',reverse('masiscam:expediente_detalle',args=[exp.pk]),'Continuar al mantenimiento'),
                                ('03-sin-asignacion',reverse('masiscam:taller_equipo',args=[otro.pk]),'ADMINISTRADOR debe asignar'),
                                ('04-sin-plano',reverse('masiscam:flujo_detalle',args=[plano_exp.pk]),'Faltan datos del equipo')]:
                                self.assertEqual(page.goto(self.live_server_url+url).status,200)
                                expect(page.get_by_text(texto,exact=False).first).to_be_visible()
                                expect(page.get_by_role('navigation',name='Migas de pan')).to_be_visible()
                                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                                self.assertLessEqual(page.locator('.mf-main-action:visible').count(),1)
                                page.screenshot(path=str(output/f'{nombre}-{width}.png'),full_page=True)
                    context.close()
            finally: browser.close()

    def test_comenzar_taller_navega_por_get_y_formulario_usa_post(self):
        from playwright.sync_api import sync_playwright, expect
        equipo=self.crear('COMENZAR-GET')
        client=Client()
        client.force_login(self.taller)
        cookie=client.cookies[settings.SESSION_COOKIE_NAME].value
        inicio_url=self.live_server_url+reverse('masiscam:taller_inicio')
        detalle_url=self.live_server_url+reverse('masiscam:taller_equipo',args=[equipo.pk])
        recepcion_url=self.live_server_url+reverse('masiscam:recepcion_crear')+f'?equipo={equipo.pk}'
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context()
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                page=context.new_page()
                requests=[]
                page.on('request',lambda request:requests.append((request.method,request.url)))
                self.assertEqual(page.goto(inicio_url).status,200)
                page.get_by_role('link',name=f'Continuar con {equipo.nombre}').click()
                expect(page).to_have_url(detalle_url)
                self.assertIn(('GET',detalle_url),requests)
                expect(page.get_by_role('link',name='Iniciar mantenimiento')).to_be_visible()
                page.get_by_role('link',name='Iniciar mantenimiento').click()
                expect(page).to_have_url(recepcion_url)
                form=page.locator('form.mf-card')
                self.assertEqual(form.evaluate('(element) => element.method'),'post')
                self.assertEqual(form.locator('[name=csrfmiddlewaretoken]').count(),1)
                expect(page.get_by_role('button',name='Continuar a inspección',exact=True)).to_be_visible()
                context.close()
            finally:
                browser.close()

    def test_iniciar_inspeccion_abre_plano_y_envia_solicitud(self):
        from playwright.sync_api import sync_playwright, expect
        from io import BytesIO
        from PIL import Image
        from . import plano_services
        equipo=self.crear('INSPECCION-POST')
        exp=self.recepcion(equipo)
        self.exp=exp
        self.planta=self.usuarios['PLANTA']
        referencia=qa.s.guardar_referencia(usuario=self.planta,empresa=self.empresa,
            clave=uuid.uuid4(),archivo=qa.pdf_plano(),datos=dict(
                codigo='REF-QA',tipo='PLANO',titulo='Plano de prueba',procedencia='Fuente QA sintética',
                autorizacion_uso='Uso y congelación QA expresamente indicados',
                proyecto_id=exp.registro.equipo.proyecto_id))
        parte=qa.s.guardar_parte(usuario=self.planta,empresa=self.empresa,
            referencia_id=referencia.pk,clave=uuid.uuid4(),datos=dict(
                posicion='A',descripcion='Componente QA',numero_parte='QA-PARTE',unidad='unidad declarada'))
        segunda_parte=qa.s.guardar_parte(usuario=self.planta,empresa=self.empresa,
            referencia_id=referencia.pk,clave=uuid.uuid4(),datos=dict(
                posicion='B',descripcion='Componente QA secundario',numero_parte='QA-PARTE-2',
                unidad='unidad declarada'))
        qa.s.estado_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=referencia.pk,
            estado='PUBLICADA',clave=uuid.uuid4())
        qa.s.aplicar_referencia(usuario=self.planta,empresa=self.empresa,referencia_id=referencia.pk,
            equipo_id=equipo.pk,fundamento='Aplicabilidad de prueba declarada',clave=uuid.uuid4())
        referencia.refresh_from_db()
        plano_services.guardar_mapa(usuario=self.planta,empresa=self.empresa,referencia_id=referencia.pk,
            clave=uuid.uuid4(),base_version=0,
            posiciones=[dict(parte=parte,pagina=1,x='.5',y='.4'),
                dict(parte=segunda_parte,pagina=1,x='.65',y='.4')])
        self.assertEqual(referencia.estado,'PUBLICADA')
        self.assertFalse(Diagnostico.objects.filter(expediente=exp).exists())
        self.assertFalse(ConfirmacionRecepcion.objects.filter(recepcion=exp.recepcion).exists())
        client=Client()
        client.force_login(self.taller)
        cookie=client.cookies[settings.SESSION_COOKIE_NAME].value
        detalle_url=self.live_server_url+reverse('masiscam:taller_equipo',args=[equipo.pk])+f'?expediente={exp.pk}'
        flujo_url=self.live_server_url+reverse('masiscam:flujo_detalle',args=[exp.pk])
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context()
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                page=context.new_page()
                self.assertEqual(page.goto(detalle_url).status,200)
                button=page.get_by_role('button',name='Iniciar inspección',exact=True)
                select=page.get_by_role('button',name='Seleccionar piezas',exact=True)
                expect(button).to_be_enabled()
                expect(select).to_be_enabled()
                form=button.locator('xpath=ancestor::form')
                self.assertEqual(form.get_attribute('action'),reverse('masiscam:flujo_detalle',args=[exp.pk]))
                self.assertEqual(form.locator('[name=csrfmiddlewaretoken]').count(),1)
                with page.expect_response(lambda response: response.url==flujo_url and response.request.method=='POST') as post:
                    button.click()
                self.assertEqual(post.value.status,302)
                expect(page).to_have_url(re.compile(r'.*/app/biblioteca-tecnica/\d+/plano/\?diagnostico=\d+&flujo=1'))
                expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=20000)
                expect(page.locator('#pl-canvas')).to_be_visible()
                page.get_by_role('link',name='Volver al trabajo').click()
                expect(page).to_have_url(flujo_url)
                select_link=page.get_by_role('link',name='Seleccionar piezas',exact=True)
                expect(select_link).to_be_enabled()
                select_link.click()
                expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=20000)
                photo=BytesIO()
                Image.new('RGB',(43,31),'purple').save(photo,'PNG')
                for index in range(2):
                    page.locator('.pl-part').nth(index).click()
                    page.get_by_role('button',name='Agregar al diagnóstico').click()
                    page.locator('#pl-position-quantity').fill('2')
                    page.locator('#pl-position-condition').fill(f'Desgaste observado en componente QA {index+1}')
                    page.locator('#pl-position-action').select_option('EVALUAR')
                    photo.seek(0)
                    page.locator('#pl-position-photos').set_input_files(dict(name=f'pieza-qa-{index+1}.png',
                        mimeType='image/png',buffer=photo.getvalue()))
                    invalid=page.locator('#pl-position-form').evaluate(
                        "form => Array.from(form.elements).filter(field => !field.checkValidity()).map(field => `${field.name}: ${field.validationMessage}`)")
                    self.assertEqual(invalid,[])
                    piece_url=page.locator('#pl-app').get_attribute('data-pieza')
                    position_url=page.locator('#pl-app').get_attribute('data-posiciones')  # un componente con posición guarda por ahí
                    with page.expect_response(lambda response:response.url.endswith((piece_url,position_url)),timeout=15000) as saved:
                        page.get_by_role('button',name='Guardar pieza').click()
                    self.assertEqual(saved.value.status,200,saved.value.text())
                    expect(page.locator('#pl-count')).to_have_text(str(index+1),timeout=15000)  # se recarga tras guardar
                page.get_by_role('link',name='Volver al trabajo').click()
                expect(page).to_have_url(flujo_url)
                # Flujo vigente: el envío a OFICINA exige validar antes el diagnóstico, y un plano provisional no se valida:
                # el botón sigue deshabilitado y el error real se muestra (no se oculta).
                # En la selección ya no hay envío: se continúa al diagnóstico y el envío aparece tras validarlo.
                expect(page.get_by_role('button',name='Enviar a OFICINA',exact=True)).to_have_count(0)
                expect(page.get_by_role('link',name='Continuar al diagnóstico')).to_be_visible()
                unico=page.locator('#mf-diagnostico')
                for nombre,valor in (('ins-estado_fisico','Estado QA'),('ins-condiciones_generales','Condiciones QA'),('ins-hallazgos','Hallazgos QA'),
                        ('fallas','Falla QA'),('causas','Causa QA'),('componentes_afectados','Componentes QA'),('recomendaciones','Recomendación QA'),('conclusiones','Conclusión QA')):
                    unico.locator(f'[name={nombre}]').fill(valor)
                unico.locator('[name=repuestos]').select_option('REQUERIDOS')
                unico.locator('[name=fecha]').fill('2026-11-20')
                unico.get_by_role('button',name='Guardar avance',exact=True).click()
                expect(page.locator('#mf-diagnostico [name=fallas]')).to_have_value('Falla QA')
                # Dos piezas seleccionadas (PDF y lista) sobre el plano provisional: validar no falla por la referencia.
                page.get_by_role('button',name=re.compile('Validar diagnóstico')).click()
                expect(page.get_by_text('No se confirmó el avance')).to_have_count(0)
                expect(page.get_by_text('ya no está publicado o aplicable')).to_have_count(0)
                context.close()
            finally:
                browser.close()
        diagnostico=Diagnostico.objects.get(expediente=exp)
        self.assertEqual(diagnostico.fallas,'Falla QA')
        self.assertTrue(diagnostico.enviado_en)
        from . import flujo_services
        flujo_services.enviar_solicitud(usuario=self.usuarios['TALLER'],empresa=exp.empresa,diagnostico_id=diagnostico.pk,
            version=diagnostico.version_edicion,clave=uuid.uuid4(),preliminar=True)
        solicitud=SolicitudRepuestos.objects.get(expediente=exp)
        self.assertEqual(len(solicitud.snapshot['piezas']),2)
        self.assertFalse(ConfirmacionRecepcion.objects.filter(recepcion=exp.recepcion).exists())
        self.assertFalse(qa.s._aprobado(diagnostico))
        oficina=Client()
        oficina.force_login(self.usuarios['OFICINA'])
        respuesta=oficina.get(reverse('masiscam:flujo_detalle',args=[exp.pk]))
        self.assertContains(respuesta,'Componente QA')
        self.assertContains(respuesta,'Componente QA secundario')
        self.assertEqual(Inspeccion.objects.filter(expediente=exp).count(),1)
        self.assertTrue(Inspeccion.objects.get(expediente=exp).enviado_en)
        self.assertFalse(ConfirmacionRecepcion.objects.filter(recepcion=exp.recepcion).exists())
