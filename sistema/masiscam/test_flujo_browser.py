"""Real browser against an isolated PostgreSQL database and fictional approvals."""
import os
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.db import connection
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client,override_settings
from django.urls import reverse
from django.utils import timezone
from . import test_flujo as qa, test_calibracion as cq, test_tecnica as tq
from . import calibracion_services as cal
from .test_operacion import presupuesto_qa
from .test_plano import foto_pieza


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1' and connection.vendor=='postgresql','PostgreSQL aislado y navegador QA requeridos')
@override_settings(**qa.FLAGS)
class FlujoBrowserTests(StaticLiveServerTestCase):
    setUp=qa.FlujoTests.setUp
    datos=qa.FlujoTests.datos
    crear=qa.FlujoTests.crear
    recepcion=qa.FlujoTests.recepcion
    preparar_solicitud=qa.FlujoTests.preparar_solicitud

    def test_cinco_pasos_hasta_informe_y_tres_resoluciones(self):
        import uuid
        from playwright.sync_api import sync_playwright,expect
        self.preparar_solicitud(plano=True,enviar=False)
        definicion=cq.definicion_qa();definicion['aplicacion'].update(marca=self.equipo.marca,modelo=self.equipo.modelo)
        protocolo=cal.crear_plantilla(usuario=self.usuarios['PLANTA'],empresa=self.empresa,clave=uuid.uuid4(),archivo=cq.protocolo_pdf(),
            datos=dict(codigo='BROWSER-QA',titulo='PROTOCOLO FICTICIO QA',tipo_equipo=self.equipo.tipo_producto,definicion=definicion,proyecto_id=self.equipo.proyecto_id))
        cal.aplicar_plantilla(usuario=self.usuarios['PLANTA'],empresa=self.empresa,plantilla_id=protocolo.pk,equipo_id=self.equipo.pk,
            clave=uuid.uuid4(),fundamento='Aplicación ficticia QA',version_equipo=definicion['aplicacion']['version_equipo'])
        policy=override_settings(MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=[cq.anchor_qa(protocolo)])
        policy.enable();self.addCleanup(policy.disable)
        revisor=tq.usuario(self.empresa,'validador-browser-flujo','PLANTA')
        cq.validar_qa(self.empresa,revisor,protocolo)
        registrado=self.crear('REGISTRO-UI-SIN-MANTENIMIENTO')
        cookies={}
        for role,user in [('TALLER',self.taller),('OFICINA',self.oficina),('PLANTA',self.usuarios['PLANTA'])]:
            client=Client();client.force_login(user);cookies[role]=client.cookies[settings.SESSION_COOKIE_NAME].value
        presupuesto=presupuesto_qa().read();aceptacion=tq.pdf_plano().read();foto=foto_pieza().read()
        fecha=timezone.localtime().strftime('%Y-%m-%dT%H:%M')
        output=Path(os.environ.get('MASISCAM_UI_EVIDENCE_DIR',settings.BASE_DIR/'.venv-rbac/flujo-evidencias'))
        output.mkdir(parents=True,exist_ok=True)
        flujo=self.live_server_url+reverse('masiscam:flujo_detalle',args=[self.exp.pk])
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            contexts=[]
            def pagina(role):
                context=browser.new_context(viewport={'width':1440,'height':1000})
                contexts.append(context)
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookies[role],url=self.live_server_url)])
                return context.new_page()
            def capturas(page,nombre,primary=1):
                for width in (1440,768,390):
                    page.set_viewport_size({'width':width,'height':1000})
                    expect(page.locator('.mf-steps > li')).to_have_count(5)
                    expect(page.locator('main .btn-primary')).to_have_count(primary)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                    page.screenshot(path=str(output/f'{nombre}-{width}.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
            def fill(form,values):
                for name,value in values.items(): form.locator(f'[name="{name}"]').fill(value)
            def primero(form,name): form.locator(f'[name="{name}"]').select_option(index=1)
            def submit(form):
                form.get_by_role('button').last.click()
                expect(form.page.locator('.mf-app')).to_be_visible()
            try:
                taller=pagina('TALLER')
                taller.goto(self.live_server_url+reverse('masiscam:taller_equipo',args=[registrado.pk]))
                capturas(taller,'00-equipo-registrado')
                expect(taller.locator('input[name=instrumentos]')).to_have_count(0)
                taller.goto(flujo)
                capturas(taller,'01-seleccionar-piezas')
                taller.get_by_role('link',name=f'Abrir plano {self.ref.codigo} · r{self.ref.revision}',exact=True).click()
                expect(taller.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=60000)
                taller.locator('#pl-plus').click();expect(taller.locator('#pl-zoom')).to_have_text('125%')
                taller.locator('.pl-point').first.click()
                expect(taller.locator('#pl-detail')).to_contain_text('QA-PARTE')
                taller.get_by_role('link',name='← Volver al trabajo',exact=True).click()
                taller.locator('.mf-main-action').click()
                expect(taller.locator('.mf-frozen').first).to_contain_text('Solicitud v1')
                capturas(taller,'02-solicitud-enviada',primary=0)
                planta=pagina('PLANTA');planta.goto(flujo)
                planta.locator('.mf-main-action').click()
                decision=planta.locator('form').filter(has=planta.get_by_role('button',name='Registrar decisión',exact=True))
                decision.locator('[name=decision]').select_option('APROBADO');decision.locator('[name=motivo]').fill('Revisión independiente ficticia QA')
                submit(decision)
                oficina=pagina('OFICINA');oficina.goto(self.live_server_url+reverse('masiscam:flujo_bandeja'))
                expect(oficina.get_by_role('heading',name='Solicitudes de taller',exact=True)).to_be_visible()
                oficina.get_by_role('link',name='Revisar solicitud',exact=True).click()
                cotizar=oficina.locator('.mf-action-form');fill(cotizar,{'referencia':'PRESUPUESTO BROWSER QA v1'})
                cotizar.locator('[name=archivo]').set_input_files(dict(name='presupuesto-qa.pdf',mimeType='application/pdf',buffer=presupuesto))
                submit(cotizar)
                aceptar=oficina.locator('.mf-action-form')
                primero(aceptar,'propuesta_id');aceptar.locator('[name=decision]').select_option('APROBADO')
                fill(aceptar,dict(fecha_cliente=fecha,representante_cliente='PERSONA FICTICIA QA',motivo='Aceptación ficticia QA',identidad='IDENTIDAD FICTICIA QA',
                    medio_verificacion='Verificación simulada QA',respaldo_verificacion='Respaldo ficticio QA, no aceptación real'))
                aceptar.locator('[name=calidad]').select_option('TITULAR');aceptar.locator('[name=canal]').select_option('PRESENCIAL_FIRMADA')
                aceptar.locator('[name=verificacion_confirmada]').check()
                aceptar.locator('[name=archivo]').set_input_files(dict(name='aceptacion-qa.pdf',mimeType='application/pdf',buffer=aceptacion))
                capturas(oficina,'03-oficina-aceptacion')
                submit(aceptar)
                adquirir=oficina.locator('.mf-action-form');primero(adquirir,'pieza_id')
                fill(adquirir,dict(cantidad='2',proveedor='PROVEEDOR FICTICIO QA',referencia='COMPRA BROWSER QA',fecha=fecha))
                submit(adquirir)
                enviar=oficina.locator('.mf-action-form');fill(enviar,dict(numero_guia='GUIA BROWSER QA',fecha=fecha))
                enviar.locator('input[name^=cantidad_]').fill('2');submit(enviar)
                capturas(oficina,'04-oficina-enviado',primary=0)
                taller.goto(flujo)
                recibir=taller.locator('.mf-action-form')
                expect(recibir.locator('input[name^=cantidad_]')).to_have_value('')
                fill(recibir,{'fecha':fecha});recibir.locator('input[name^=cantidad_]').fill('2')
                capturas(taller,'05-confirmar-recepcion')
                submit(recibir)
                taller.locator('.mf-main-action').click()
                actividad=taller.locator('form[enctype="multipart/form-data"]').last
                fill(actividad,dict(trabajo='Reparación y montaje ficticios QA',componentes='Componente QA',inicio=fecha));submit(actividad)
                taller.get_by_role('link',name='Registrar repuesto utilizado',exact=True).click()
                uso=taller.locator('form[enctype="multipart/form-data"]').last
                primero(uso,'actividad_id');primero(uso,'recepcion_id');fill(uso,dict(cantidad='2',observaciones='Uso ficticio QA'));submit(uso)
                taller.get_by_role('link',name='Adjuntar fotografía de reparación',exact=True).click()
                evidencia=taller.locator('form[enctype="multipart/form-data"]').last
                primero(evidencia,'actividad_id');evidencia.locator('[name=clase]').select_option('DURANTE');fill(evidencia,dict(leyenda='Montaje ficticio QA'))
                evidencia.locator('[name=archivo]').set_input_files(dict(name='montaje-qa.png',mimeType='image/png',buffer=foto));submit(evidencia)
                taller.locator('.mf-main-action').click()
                resultado=taller.locator('form[enctype="multipart/form-data"]').last
                primero(resultado,'actividad_id');fill(resultado,dict(fin=fecha,trabajo_realizado='Trabajo ficticio QA',resultado='Resultado ficticio QA'));submit(resultado)
                planta.goto(flujo);planta.locator('.mf-main-action').click()
                revision=planta.locator('form[enctype="multipart/form-data"]').last
                primero(revision,'resultado_id');revision.locator('[name=decision]').select_option('APROBADO');fill(revision,dict(motivo='Revisión independiente ficticia QA'));submit(revision)
                taller.goto(flujo);capturas(taller,'06-preparar-calibracion')
                taller.locator('.mf-main-action').click()
                inicio=taller.locator('form').filter(has=taller.get_by_role('button',name='Solicitar inicio validado',exact=True))
                inicio.locator('[name=plantilla_id]').select_option(str(protocolo.pk));submit(inicio)
                datos=taller.locator('form:has(input[name=op][value=datos])')
                fill(datos,dict(fecha=fecha,instrumentos='INSTRUMENTO FICTICIO QA',resultados='Resultados ficticios QA',motivo_sin_evidencia='Solo fotografías previas QA',param_0='0'));submit(datos)
                for n in range(1,17):
                    medir=taller.locator('form:has(input[name=op][value=medir])')
                    medir.locator('[name=punto]').select_option(f'QA-{n:02}');fill(medir,dict(valor='0',observaciones='Medición ficticia QA'));submit(medir)
                taller.get_by_role('button',name='Enviar 16 mediciones a revisión',exact=True).click()
                planta.goto(flujo);planta.locator('.mf-main-action').click()
                evaluar=planta.locator('form:has(input[name=op][value=decision])')
                evaluar.locator('[name=decision]').select_option('APROBADA');fill(evaluar,dict(motivo='Evaluación humana ficticia QA, sin valor industrial'))
                for select in evaluar.locator('select[name^=eval_]').all(): select.select_option('CONFORME')
                submit(evaluar)
                taller.goto(flujo);capturas(taller,'07-informe-final')
                with taller.expect_download() as download:
                    taller.locator('.mf-main-action').click()
                self.assertTrue(download.value.suggested_filename.endswith('.pdf'))
            finally:
                for context in contexts: context.close()
                browser.close()
