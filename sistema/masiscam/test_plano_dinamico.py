"""Selección manual desde el dibujo; fixtures existentes, sin OCR ni servicios externos."""
import json
import os
import uuid
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .test_tres_roles import TresRolesFixture
from . import test_flujo, test_plano_trabajo as qa
from .models import Auditoria, Documento, MapaPlano, PosicionPlano, ReferenciaTecnica


class DinamicoFixture(TresRolesFixture):
    def guardar(self, **extra):
        self.diag.refresh_from_db()
        mapa = self.ref.mapas.order_by('-version').first()
        data = dict(clave=str(uuid.uuid4()), diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion, mapa_version=mapa.version if mapa else 0,
            operacion='guardar', pagina=1,x='.7886',y='.3388',posicion='7',
            descripcion='HEAT EXCHANGER',cantidad='2',unidad='unidad',condicion='Daño observado QA')
        data.update(extra)
        photos=data.pop('fotografias',None)
        if photos is not None:
            return self.client.post(reverse('masiscam:plano_posicion',args=[self.ref.pk]),dict(data,fotografias=photos))
        return self.client.post(reverse('masiscam:plano_posicion',args=[self.ref.pk]),
            json.dumps(data),content_type='application/json')


@override_settings(**test_flujo.FLAGS)
class PlanoDinamicoTests(DinamicoFixture, TestCase):
    def test_subir_correccion_reutiliza_versionado_y_abre_visor(self):
        from .plano_services import decidir_plano_trabajo
        self.cargar()
        decidir_plano_trabajo(usuario=self.admin,empresa=self.empresa,referencia_id=self.ref.pk,
            clave=uuid.uuid4(),revision=1,decision='RECHAZADO',motivo='Corregir legibilidad QA')
        from django.core.files.uploadedfile import SimpleUploadedFile
        contenido=self.pdf  # el reintento reenvía el mismo archivo cargado; no crea otro documento
        documentos=Documento.objects.count()
        key=uuid.uuid4()
        for _ in range(2):
            response=self.client.post(self.url,dict(op='plano_trabajo',clave=key,
                referencia_id=self.ref.pk,archivo=SimpleUploadedFile('plano-qa.pdf',contenido,content_type='application/pdf')))
            nueva=self.exp.planos_trabajo.get(sustituye=self.ref)
            self.assertRedirects(response,reverse('masiscam:plano',args=[nueva.pk])+f'?diagnostico={self.diag.pk}&flujo=1')
        self.assertEqual(self.exp.planos_trabajo.count(),2)  # Fuente y corrección auditada.
        self.assertEqual(self.exp.diagnostico_set.count(),1)
        self.assertEqual(Documento.objects.count(),documentos)

    def test_autocompleta_desde_catalogo_vinculado_sin_modificarlo(self):
        from decimal import Decimal
        from . import tecnica_services as tecnica
        from .test_tecnica import pdf_plano
        self.cargar()
        base = dict(usuario=self.admin, empresa=self.empresa)
        cat = tecnica.guardar_referencia(**base, clave=uuid.uuid4(), archivo=pdf_plano(), datos=dict(codigo='CAT-AUTO', tipo='PLANO',
            titulo='Catálogo QA', marca='M', modelo='X', rotacion='#1', ratio='3:1', procedencia='QA', autorizacion_uso='QA',
            proyecto_id=self.equipo.proyecto_id))
        tecnica.guardar_parte(**base, referencia_id=cat.pk, clave=uuid.uuid4(), datos=dict(posicion='4', descripcion='Rodamiento de catálogo',
            numero_parte='COD-4', unidad='u', cantidad_referencia='3'))
        url = reverse('masiscam:plano_catalogo_parte', args=[self.ref.pk])
        def consulta(pos):
            # La consulta es de solo lectura: select_for_update fuera de una transacción daría 500 en producción.
            from unittest.mock import patch
            with patch('django.db.models.query.QuerySet.select_for_update', side_effect=AssertionError('bloqueo en consulta')):
                return self.client.get(url, {'diagnostico': self.diag.pk, 'posicion': pos})
        # Sin catálogo vinculado aún.
        self.assertEqual(consulta('4').json()['estado'], 'sin_catalogo')
        tecnica.estado_referencia(**base, referencia_id=cat.pk, estado='PUBLICADA', clave=uuid.uuid4())
        tecnica.aplicar_referencia(**base, referencia_id=cat.pk, equipo_id=self.equipo.pk, fundamento='Vínculo QA', clave=uuid.uuid4())
        self.client.force_login(self.taller)
        r = consulta(' 4 ').json()
        self.assertEqual((r['estado'], r['codigo'], r['descripcion'], r['unidad'], Decimal(r['cantidad_referencia']), r['referencia']),
                         ('encontrada', 'COD-4', 'Rodamiento de catálogo', 'u', Decimal('3'), dict(codigo='CAT-AUTO', revision=1)))
        self.assertEqual(consulta('99').json()['estado'], 'sin_posicion')  # coincidencia exacta de posición
        self.assertEqual(consulta('').status_code, 400)
        self.assertEqual(self.client.get(url, {'diagnostico': 'x', 'posicion': '4'}).status_code, 404)
        self.client.force_login(self.oficina)
        self.assertEqual(consulta('4').status_code, 403)
        self.client.force_login(self.taller)
        # Al guardar, el código y la cantidad referencial salen del catálogo; el maestro no cambia.
        self.assertEqual(self.guardar(posicion='4', descripcion='Rodamiento de catálogo', unidad='').status_code, 200)
        parte = self.ref.partes.get(posicion='4')
        self.assertEqual((parte.numero_parte, parte.unidad, parte.cantidad_referencia, parte.referencia_id), ('COD-4', 'u', Decimal('3'), self.ref.pk))
        maestro = cat.partes.get()
        self.assertEqual((maestro.numero_parte, maestro.descripcion, cat.partes.count()), ('COD-4', 'Rodamiento de catálogo', 1))
        self.assertEqual(self.diag.piezas.count(), 1)
        # Repetir el número no duplica componente ni pieza.
        self.assertEqual(self.guardar(posicion='4', descripcion='Rodamiento de catálogo', x='.2', y='.2').status_code, 200)
        self.assertEqual((self.ref.partes.filter(posicion='4').count(), self.diag.piezas.count()), (1, 1))
        # Una posición ausente se registra a mano, sin código ni cambios en el catálogo.
        self.assertEqual(self.guardar(posicion='99', descripcion='Pieza manual', x='.5', y='.5').status_code, 200)
        manual = self.ref.partes.get(posicion='99')
        self.assertEqual((manual.numero_parte, manual.cantidad_referencia), ('', None))
        self.assertEqual(cat.partes.count(), 1)

    def test_taller_formulario_unico_guardar_validar_y_sin_textos_de_aprobacion(self):
        from .models import Diagnostico, Inspeccion
        self.cargar()
        r = self.client.get(self.url)
        html = r.content.decode()
        for ausente in ('Evidencias de inspección', 'Etapas de TALLER', 'Guardar detalles técnicos', 'Validar y enviar inspección'):
            self.assertNotIn(ausente, html)
        for texto in ('pendiente de aprobación', 'debe aprobar', 'requiere aprobación', 'plano no aprobado', 'se mantienen los requisitos de aprobación'):
            self.assertNotIn(texto, html.lower(), texto)
        self.assertContains(r, 'Diagnóstico e información técnica')
        self.assertEqual(html.count('id="mf-diagnostico"'), 1)
        fila = html[html.index('mf-acciones-form'):]
        fila = fila[:fila.index('</div>')]
        self.assertIn('Guardar avance', fila)
        self.assertIn('Validar diagnóstico con ADMINISTRADOR', fila)
        forms = r.context['forms']
        self.assertIn('diagnostico', forms)

        def datos(**cambios):
            valores = {}
            for nombre in ('diagnostico', 'inspeccion'):
                if nombre in forms:
                    valores.update({bf.html_name: ('' if bf.value() is None else bf.value()) for bf in forms[nombre]})
            valores.update(cambios)
            return valores

        base = dict(op='guardar', fallas='Daño QA', causas='Desgaste QA', componentes_afectados='Eje QA', recomendaciones='Cambiar QA',
                    conclusiones='Conclusión QA', repuestos='SIN_REPUESTOS', fecha='2026-11-20')
        if 'inspeccion' in forms:
            base.update({'ins-estado_fisico': 'Estado QA', 'ins-condiciones_generales': 'Condiciones QA', 'ins-hallazgos': 'Hallazgos QA'})
        r = self.client.post(self.url, datos(**base))
        self.assertEqual(r.status_code, 302, __import__('re').findall(r'(?:alert-danger|mf-error|text-danger)[^<]*<[^>]*>[^<]{0,160}|<li>[^<]{5,160}', r.content.decode()) if r.status_code != 302 else '')
        diag = Diagnostico.objects.get(pk=self.diag.pk)
        self.assertEqual((diag.fallas, diag.causas, diag.enviado_en), ('Daño QA', 'Desgaste QA', None))
        if 'inspeccion' in forms:
            ins = Inspeccion.objects.get(pk=diag.inspeccion_id)
            self.assertEqual((ins.estado_fisico, ins.hallazgos), ('Estado QA', 'Hallazgos QA'))
            self.assertTrue(ins.fecha)  # la fecha del diagnóstico se aplica a la inspección
        # Un error real de validación no se oculta ni se envía nada: sin fallas la validación no pasa.
        self.client.get(self.url)
        forms = self.client.get(self.url).context['forms']
        r = self.client.post(self.url, datos(**dict(base, op='validar', fallas='')))
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, 'Campo obligatorio', status_code=400)
        self.assertIsNone(Diagnostico.objects.get(pk=self.diag.pk).enviado_en)
        # Validar: envía inspección y diagnóstico sin exigir evidencias de inspección.
        self.client.get(self.url)
        r = self.client.get(self.url)
        forms = r.context['forms']
        r = self.client.post(self.url, datos(**dict(base, op='validar')))
        self.assertEqual(r.status_code, 302, __import__('re').findall(r'(?:alert-danger|mf-error|text-danger)[^<]*<[^>]*>[^<]{0,160}|<li>[^<]{5,160}', r.content.decode()) if r.status_code != 302 else '')
        diag.refresh_from_db()
        self.assertIsNotNone(diag.enviado_en)
        ins = Inspeccion.objects.get(pk=diag.inspeccion_id)
        self.assertIsNotNone(ins.enviado_en)
        # El motivo automático queda guardado y auditado; no se crean evidencias ficticias.
        from .flujo_views import MOTIVO_INSPECCION_SIN_EVIDENCIA
        from .models import Auditoria
        self.assertEqual((diag.motivo_sin_evidencia, ins.motivo_sin_evidencia), (MOTIVO_INSPECCION_SIN_EVIDENCIA,) * 2)
        self.assertEqual((diag.evidencias.count(), ins.evidencias.count()), (0, 0))
        self.assertTrue(Auditoria.objects.filter(expediente=self.exp, accion='REVISION_GUARDADA', usuario=self.taller).count() >= 3)
        self.assertEqual(Auditoria.objects.filter(expediente=self.exp, accion='REVISION_ENVIADA', usuario=self.taller).count(), 2)

    def test_posicion_con_fotos_atomica_reintento_y_ux_sin_duplicados(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.cargar()
        response=self.guardar(fotografias=[SimpleUploadedFile('invalida.png',b'no es una imagen',content_type='image/png')])
        self.assertEqual(response.status_code,400)
        self.assertFalse(self.ref.partes.exists());self.assertFalse(self.ref.mapas.exists())
        self.assertFalse(self.diag.piezas.exists())
        key=str(uuid.uuid4());self.diag.refresh_from_db();version=self.diag.version_edicion
        for _ in range(2):
            response=self.guardar(clave=key,version=version,mapa_version=0,fotografias=[qa.foto_pieza()])
            self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.ref.partes.count(),1);self.assertEqual(self.ref.mapas.count(),1)
        self.assertEqual(self.diag.piezas.count(),1)
        self.assertEqual(self.diag.piezas.get().fotografias.count(),1)
        response=self.client.get(self.url)
        for text in ('Subir plano PDF','Seleccionar piezas','Enviar a OFICINA'):
            self.assertContains(response,text)
        for text in ('Agregar pieza manualmente','ADMINISTRADOR debe','detalles-tecnicos'):
            self.assertNotContains(response,text)
        self.assertNotContains(self.client.get(self.plano_url),'pl-config-toggle')

    def test_registrar_seleccionar_editar_retirar_y_conservar_versiones(self):
        self.cargar()
        self.assertFalse(self.ref.partes.exists())
        directo=self.client.get(reverse('masiscam:plano_datos',args=[self.ref.pk])).json()
        self.assertTrue(directo['marcable'])
        key=str(uuid.uuid4())
        self.assertEqual(self.guardar(clave=key).status_code,200)
        punto=self.ref.mapas.get().posiciones.get()
        pieza=self.diag.piezas.get()
        self.assertEqual(pieza.parte.descripcion,'HEAT EXCHANGER')
        self.assertEqual(str(pieza.cantidad),'2.000000')
        self.assertEqual(pieza.posicion_plano_id,punto.pk)
        # Doble envío con la misma clave/contenido no duplica resultados.
        data=dict(clave=key,diagnostico_id=self.diag.pk,version=1,mapa_version=0,operacion='guardar',
            pagina=1,x='.7886',y='.3388',posicion='7',descripcion='HEAT EXCHANGER',cantidad='2',unidad='unidad',condicion='Daño observado QA')
        self.assertEqual(self.client.post(reverse('masiscam:plano_posicion',args=[self.ref.pk]),
            json.dumps(data),content_type='application/json').status_code,200)
        self.assertEqual(self.diag.piezas.count(),1)
        self.assertEqual(self.guardar(operacion='seleccionar',punto_id=punto.pk).status_code,200)
        self.assertEqual(self.diag.piezas.count(),1)
        self.assertEqual(self.guardar(punto_id=punto.pk,parte_id=pieza.parte_id,x='.7',cantidad='3').status_code,200)
        nuevo=self.ref.mapas.order_by('-version').first().posiciones.get()
        self.assertNotEqual(nuevo.pk,punto.pk)
        punto.refresh_from_db();self.assertEqual(str(punto.x),'0.78860000')
        pieza.refresh_from_db();self.assertEqual(pieza.cantidad,3)
        self.assertEqual(pieza.posicion_plano_id,nuevo.pk)
        self.assertEqual(self.guardar(operacion='retirar',punto_id=nuevo.pk).status_code,200)
        self.assertFalse(self.ref.mapas.order_by('-version').first().posiciones.exists())
        self.assertEqual(self.ref.partes.count(),1)
        self.assertEqual(self.diag.piezas.count(),1)
        self.assertEqual(PosicionPlano.objects.filter(mapa__referencia=self.ref).count(),2)
        self.assertEqual(Auditoria.objects.filter(accion='PLANO_POSICION_TRABAJO').count(),4)

    def test_plano_aprobado_vacio_continua_en_borrador_sin_alterar_aprobacion(self):
        self.cargar()
        from .plano_services import decidir_plano_trabajo
        decidir_plano_trabajo(usuario=self.admin,empresa=self.empresa,referencia_id=self.ref.pk,
            clave=uuid.uuid4(),revision=1,decision='APROBADO')
        documentos=Documento.objects.count()
        response=self.guardar();self.assertEqual(response.status_code,200,response.content)
        self.ref.refresh_from_db();self.assertEqual(self.ref.estado,'PUBLICADA')
        self.assertTrue(self.ref.aplicabilidades.get().activa)
        self.assertFalse(self.ref.partes.exists())
        nueva=self.exp.planos_trabajo.get(sustituye=self.ref)
        self.assertEqual(nueva.estado,'BORRADOR')
        self.assertTrue(nueva.mapas.get().provisional)
        self.assertEqual(nueva.documento_id,self.ref.documento_id)
        self.assertEqual(Documento.objects.count(),documentos)
        self.assertIn(f'/{nueva.pk}/plano/',response.json()['plano_url'])

    def test_permiso_coordenadas_conflicto_y_envio_congelado(self):
        self.cargar()
        self.assertEqual(self.guardar(x='1.1').status_code,400)
        self.assertEqual(self.guardar(pagina=201).status_code,400)
        self.assertFalse(self.ref.partes.exists())
        for user in (self.oficina,self.admin,self.otro):
            self.client.force_login(user)
            self.assertEqual(self.guardar().status_code,403)
        self.client.force_login(self.taller)
        self.assertEqual(self.guardar().status_code,200)
        self.assertEqual(self.guardar(mapa_version=0).status_code,400)
        self.diag.refresh_from_db()
        from .flujo_services import enviar_solicitud
        sol=enviar_solicitud(usuario=self.taller,empresa=self.empresa,diagnostico_id=self.diag.pk,
            version=self.diag.version_edicion,clave=uuid.uuid4(),preliminar=True)
        snapshot=sol.snapshot
        self.assertEqual(self.guardar().status_code,400)
        sol.refresh_from_db();self.assertEqual(sol.snapshot,snapshot)
        self.client.force_login(self.oficina)
        self.assertContains(self.client.get(self.url),'HEAT EXCHANGER')

    def test_biblioteca_aprobada_reutiliza_pdf_sin_equipo_solo_si_es_aplicable(self):
        from . import tecnica_services as t
        self.equipo=self.crear('BIBLIOTECA-DINAMICA');self.exp=self.recepcion(self.equipo)
        _,self.diag=t.iniciar_inspeccion(usuario=self.taller,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4())
        self.ref=t.guardar_referencia(usuario=self.admin,empresa=self.empresa,clave=uuid.uuid4(),archivo=qa.pdf_trabajo(),
            datos=dict(codigo='DERAN-BIBLIOTECA-QA',tipo='PLANO',titulo='DERAN QA',procedencia='PDF real QA',
                autorizacion_uso='Prueba aislada',proyecto_id=self.equipo.proyecto_id))
        t.estado_referencia(usuario=self.admin,empresa=self.empresa,referencia_id=self.ref.pk,estado='PUBLICADA',clave=uuid.uuid4())
        with transaction.atomic(),self.assertRaises(IntegrityError):
            ReferenciaTecnica.objects.create(empresa=self.empresa,documento=self.ref.documento,
                expediente_trabajo=self.exp,autor=self.taller,sustituye=self.ref,
                codigo=self.ref.codigo,revision=2,tipo='PLANO',titulo='No aplicable QA')
        t.aplicar_referencia(usuario=self.admin,empresa=self.empresa,referencia_id=self.ref.pk,equipo_id=self.equipo.pk,
            fundamento='Equipo de QA aislado',clave=uuid.uuid4())
        self.assertIsNone(self.ref.documento.equipo_id)
        documentos=Documento.objects.count()
        response=self.guardar();self.assertEqual(response.status_code,200,response.content)
        nueva=self.exp.planos_trabajo.get(sustituye=self.ref)
        self.assertEqual(nueva.documento_id,self.ref.documento_id)
        self.assertEqual(Documento.objects.count(),documentos)


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1' and qa.PDF_REAL, 'PDF DERAN real y navegador explícitos')
@override_settings(**test_flujo.FLAGS)
class PlanoDinamicoBrowserTests(DinamicoFixture, StaticLiveServerTestCase):
    def test_editar_fotografia_sin_duplicar_varias_piezas_y_enviar(self):
        from playwright.sync_api import sync_playwright, expect
        from .models import SolicitudRepuestos
        self.cargar()
        parte=self.componente();pieza=self.seleccionar(parte)
        foto_id=pieza.fotografias.get().pk
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,
                    value=self.client.cookies[settings.SESSION_COOKIE_NAME].value,url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+self.plano_url)
                expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=30000)
                expect(page.locator('.pl-part')).to_contain_text('M2021E')
                for initial,amount in (('2','3'),('3','3')):
                    page.locator('.pl-selected-card').get_by_role('button',name='Editar',exact=True).click()
                    sheet=page.locator('#pl-piece-form')
                    expect(sheet.locator('[name=cantidad]')).to_have_value(initial)
                    sheet.locator('[name=cantidad]').fill(amount)
                    photo=qa.foto_pieza()
                    sheet.locator('[name=fotografias]').set_input_files(dict(name=photo.name,mimeType=photo.content_type,buffer=photo.read()))
                    sheet.get_by_role('button',name='Guardar pieza',exact=True).click()
                    expect(sheet).not_to_be_visible()
                expect(page.locator('.pl-selected-card')).to_contain_text('3 unidad')
                page.locator('#pl-stage').click(position=dict(x=220,y=200))
                sheet=page.locator('#pl-position-form')
                sheet.locator('[name=posicion]').fill('8')
                sheet.locator('[name=descripcion]').fill('RADIAL BEARING CARRIER')
                sheet.locator('[name=cantidad]').fill('1')
                sheet.get_by_text('Unidad (opcional)',exact=True).click()
                sheet.locator('[name=condicion]').fill('Daño observado QA')
                sheet.locator('[name=unidad]').fill('unidad')
                sheet.get_by_role('button',name='Guardar posición y seleccionar',exact=True).click()
                expect(sheet).not_to_be_visible();expect(page.locator('#pl-count')).to_have_text('2')
                page.reload();expect(page.locator('#pl-app')).to_have_attribute('data-ready','true')
                expect(page.locator('#pl-count')).to_have_text('2')
                page.locator('.pl-point').click();expect(page.locator('#pl-count')).to_have_text('2')
                data_url=page.locator('#pl-app').get_attribute('data-datos')
                draft=page.request.get(self.live_server_url+data_url).json()
                self.assertEqual(draft['estado'],'EN_PROCESO')
                self.assertTrue(draft['editable'])  # Sin envío ni solicitud congelada.
                page.goto(self.live_server_url+self.url)
                page.locator('#mf-envio').get_by_role('button',name='Enviar a OFICINA',exact=True).click()
                confirm=page.locator('.swal2-confirm')
                if confirm.is_visible():confirm.click()
                expect(page.locator('.mf-frozen')).to_contain_text('Solicitud v')
            finally:browser.close()
        self.assertEqual(self.diag.piezas.count(),2)
        self.assertEqual(pieza.fotografias.count(),1)
        self.assertEqual(pieza.fotografias.get().pk,foto_id)
        self.assertTrue(SolicitudRepuestos.objects.filter(diagnostico=self.diag).exists())
        self.client.force_login(self.oficina)
        self.assertContains(self.client.get(self.url),'HEAT EXCHANGER')

    def test_pdf_real_clic_registro_zoom_recarga_seleccion_y_edicion(self):
        from playwright.sync_api import sync_playwright,expect
        self.cargar()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,
                    value=self.client.cookies[settings.SESSION_COOKIE_NAME].value,url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+self.plano_url)
                expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=30000)
                expect(page.locator('#pl-total')).to_have_text('0')
                page.locator('#pl-plus').click();page.locator('#pl-plus').click()
                expect(page.locator('#pl-zoom')).to_have_text('150%')
                def click_pdf(x,y):
                    vp=page.locator('#pl-viewport');vp.scroll_into_view_if_needed()
                    vp.evaluate('(vp,p)=>{const s=vp.querySelector("#pl-stage");vp.scrollLeft=s.clientWidth*p.x-vp.clientWidth/2;vp.scrollTop=s.clientHeight*p.y-vp.clientHeight/2;}',dict(x=x,y=y))
                    bounds=page.locator('#pl-stage').bounding_box()
                    page.mouse.click(bounds['x']+bounds['width']*x,bounds['y']+bounds['height']*y)
                click_pdf(.7886,.3388)
                modal=page.locator('#pl-position-dialog');expect(modal).to_be_visible()
                modal.locator('[name=posicion]').fill('7')
                modal.locator('[name=descripcion]').fill('HEAT EXCHANGER')
                modal.locator('[name=cantidad]').fill('2')
                if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                    page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'registro-desde-pdf.png'),full_page=True)
                modal.get_by_text('Unidad (opcional)',exact=True).click()
                modal.locator('[name=condicion]').fill('Daño observado QA')
                modal.locator('[name=unidad]').fill('unidad')
                modal.get_by_role('button',name='Guardar posición y seleccionar',exact=True).click()
                expect(modal).not_to_be_visible()
                expect(page.locator('#pl-total')).to_have_text('1')
                expect(page.locator('#pl-count')).to_have_text('1')
                expect(page.locator('.pl-point')).to_have_count(1)
                page.reload();expect(page.locator('#pl-app')).to_have_attribute('data-ready','true')
                page.locator('#pl-plus').click();expect(page.locator('#pl-zoom')).to_have_text('125%')
                page.locator('.pl-point').scroll_into_view_if_needed()
                stage=page.locator('#pl-stage').bounding_box();point=page.locator('.pl-point').bounding_box()
                self.assertAlmostEqual((point['x']+point['width']/2-stage['x'])/stage['width'],.7886,delta=.002)
                self.assertAlmostEqual((point['y']+point['height']/2-stage['y'])/stage['height'],.3388,delta=.002)
                page.get_by_role('button',name='Retirar de borrador',exact=True).click()
                expect(page.locator('#pl-count')).to_have_text('0')
                page.locator('.pl-point').click();expect(page.locator('#pl-count')).to_have_text('1')
                page.locator('.pl-point').click();expect(page.locator('#pl-count')).to_have_text('1')
                page.get_by_role('button',name='Editar posición',exact=True).click()
                expect(modal).to_be_visible();modal.locator('[name=cantidad]').fill('3')
                modal.get_by_role('button',name='Mover en el plano',exact=True).click()
                click_pdf(.7,.4);expect(modal).to_be_visible()
                modal.get_by_role('button',name='Guardar posición y seleccionar',exact=True).click()
                expect(modal).not_to_be_visible()
                expect(page.locator('.pl-selected-card')).to_contain_text('3 unidad')
                page.get_by_role('button',name='Eliminar posición provisional',exact=True).click()
                page.locator('.swal2-confirm').click();expect(page.locator('.pl-point')).to_have_count(0)
                page.reload();expect(page.locator('#pl-app')).to_have_attribute('data-ready','true')
                expect(page.locator('.pl-point')).to_have_count(0)
                expect(page.locator('#pl-count')).to_have_text('1')
                page.locator('#pl-search').fill('7');expect(page.locator('.pl-part')).to_have_count(1)
                if os.environ.get('MASISCAM_UI_EVIDENCE_DIR'):
                    page.screenshot(path=str(Path(os.environ['MASISCAM_UI_EVIDENCE_DIR'])/'seleccion-dinamica.png'),full_page=True)
            finally:browser.close()
        self.assertEqual(self.diag.piezas.count(),1)
        self.assertEqual(self.diag.piezas.get().cantidad,3)
        self.assertGreaterEqual(MapaPlano.objects.filter(referencia=self.ref).count(),3)

    def test_clic_desde_plano_aprobado_sin_diagnostico_en_url(self):
        from playwright.sync_api import sync_playwright,expect
        from .plano_services import decidir_plano_trabajo
        self.cargar()
        decidir_plano_trabajo(usuario=self.admin,empresa=self.empresa,referencia_id=self.ref.pk,
            clave=uuid.uuid4(),revision=1,decision='APROBADO')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(viewport=dict(width=1440,height=1000))
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,
                    value=self.client.cookies[settings.SESSION_COOKIE_NAME].value,url=self.live_server_url)])
                page=context.new_page();page.goto(self.live_server_url+reverse('masiscam:plano',args=[self.ref.pk]))
                expect(page.locator('#pl-app')).to_have_attribute('data-ready','true',timeout=30000)
                navigations=[];native=[]
                page.on('request',lambda req:navigations.append(req.url) if req.is_navigation_request() else None)
                page.on('dialog',lambda dialog:(native.append(dialog.type),dialog.dismiss()))
                page.evaluate('window.viewerToken="same-viewer"')
                page.locator('#pl-plus').click();expect(page.locator('#pl-zoom')).to_have_text('125%')
                page.locator('#pl-stage').click(position=dict(x=180,y=180))
                form=page.locator('#pl-position-form');expect(form).to_be_visible()
                form.locator('[name=posicion]').fill('7');form.locator('[name=descripcion]').fill('HEAT EXCHANGER')
                form.locator('[name=cantidad]').fill('2')
                scroll=page.locator('#pl-viewport').evaluate('(v)=>{v.scrollLeft=80;return [v.scrollLeft,v.scrollTop]}')
                form.get_by_role('button',name='Guardar posición y seleccionar',exact=True).click()
                expect(page.locator('#pl-total')).to_have_text('1')
                expect(page.locator('#pl-app')).to_have_attribute('data-ready','true')
                expect(page.locator('#pl-count')).to_have_text('1')
                expect(page.get_by_text('Plano de trabajo.',exact=True)).to_be_visible()
                expect(page.locator('#pl-zoom')).to_have_text('125%')
                self.assertEqual(page.evaluate('window.viewerToken'),'same-viewer')
                self.assertEqual(page.locator('#pl-viewport').evaluate('(v)=>[v.scrollLeft,v.scrollTop]'),scroll)
                self.assertFalse(navigations)
                self.assertFalse(native)
                page.reload();expect(page.locator('#pl-app')).to_have_attribute('data-ready','true')
                expect(page.locator('.pl-point')).to_have_count(1)
                self.assertFalse(native)
                page.locator('#pl-stage').click(position=dict(x=220,y=220))
                form.locator('[name=posicion]').fill('8')
                with page.expect_event('dialog') as warning:
                    page.evaluate('setTimeout(()=>location.reload(),0)')
                self.assertEqual(warning.value.type,'beforeunload')
                self.assertEqual(native,['beforeunload'])
                # Dismissed navigation keeps the unfinished form and its values.
                expect(form.locator('[name=posicion]')).to_have_value('8')
            finally:browser.close()
        self.ref.refresh_from_db();self.assertEqual(self.ref.estado,'PUBLICADA')
        self.assertTrue(self.exp.planos_trabajo.get(sustituye=self.ref).mapas.get().provisional)
