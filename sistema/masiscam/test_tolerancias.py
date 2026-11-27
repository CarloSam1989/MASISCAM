"""Tolerancias de calibración configuradas por OFICINA y ADMINISTRADOR: versiones, auditoría y comparación automática de las 16 mediciones. Solo QA sintético."""
import os
import uuid
from decimal import Decimal
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import DatabaseError,transaction
from django.test import Client,TestCase,override_settings
from django.urls import reverse
from .models import *
from . import calibracion_services as s
from .test_calibracion import FLAGS,HojaBase


def config(eq):
    return dict(tipo_equipo=eq.tipo_producto,marca=eq.marca,modelo=eq.modelo,ratio=eq.ratio,rotacion=eq.rotacion)


class ToleranciasBase(HojaBase):
    def setUp(self):
        super().setUp()
        self.admin=self.planta   # el rol PLANTA de la fixture es el ADMINISTRADOR
        Equipo.objects.filter(pk=self.exp.registro.equipo_id).update(ratio='25.4',rotacion='Horaria')
        self.equipo=Equipo.objects.get(pk=self.exp.registro.equipo_id)

    def datos(self,**extra):
        base=dict(config(self.equipo),unidad_longitud='mm',backlash_min='0.10',backlash_max='0.20',tolerancia='0.05',calibracion_min='0.05',calibracion_max='0.08',
            unidad_presion='psi',presion_vertical_min='30',presion_vertical_max='40',presion_horizontal_min='35',presion_horizontal_max='38',
            fuente='Manual FICTICIO QA, sección 4',mediciones_parametro='BACKLASH',mediciones_criterio='Procedimiento FICTICIO QA 7.2: las 16 lecturas son de backlash',motivo='')
        base.update(extra);return base

    def registrar(self,usuario=None,**extra):
        return s.registrar_tolerancia(usuario=usuario or self.oficina,empresa=self.empresa,clave=extra.pop('clave',uuid.uuid4()),datos=self.datos(**extra))

    def hoja(self,**extra):
        c=self.abrir();self.llenar(c);self.finalizar(c);c.refresh_from_db();return c,self.generar(c,**extra)



@override_settings(**FLAGS)
class ToleranciasTests(ToleranciasBase,TestCase):
    # ------------------------------------------------------------------ configuración, versiones y auditoría
    def test_oficina_y_administrador_configuran_taller_no_y_cada_cambio_es_una_version(self):
        with self.assertRaises(PermissionDenied): self.registrar(usuario=self.tecnico)
        self.assertFalse(ToleranciaCalibracion.objects.exists())
        v1=self.registrar();self.assertEqual((v1.version,v1.sustituye_id,v1.activa,v1.autor_id),(1,None,True,self.oficina.pk))
        self.assertEqual(s.tolerancia_vigente(self.empresa,self.equipo).pk,v1.pk)
        with self.assertRaises(ValidationError): self.registrar(usuario=self.admin,backlash_max='0.25')   # un cambio exige motivo
        v2=self.registrar(usuario=self.admin,backlash_max='0.25',motivo='Ajuste según la revisión del manual')
        self.assertEqual((v2.version,v2.sustituye_id,v2.autor_id),(2,v1.pk,self.admin.pk));v1.refresh_from_db();self.assertEqual(str(v1.backlash_max),'0.200000')   # la versión 1 se conserva
        self.assertEqual(s.tolerancia_vigente(self.empresa,self.equipo).pk,v2.pk);self.assertNotEqual(v1.huella,v2.huella)
        # Configuración con mayúsculas y espacios distintos = misma configuración (nueva versión, no una paralela).
        v3=self.registrar(marca='  '+self.equipo.marca.upper()+'  ',motivo='Normalización');self.assertEqual(v3.version,3)
        self.assertEqual(ToleranciaCalibracion.objects.values('configuracion').distinct().count(),1)
        # Auditoría con responsable, versión, huella y motivo.
        auditoria=Auditoria.objects.filter(empresa=self.empresa,accion='CAL_TOLERANCIA').order_by('pk')
        self.assertEqual([a.detalle['version'] for a in auditoria],[1,2,3]);self.assertEqual(auditoria[1].usuario_id,self.admin.pk)
        self.assertEqual((auditoria[1].detalle['motivo'],auditoria[1].detalle['huella_registro']),('Ajuste según la revisión del manual',v2.huella))

    def test_idempotencia_y_conflicto_de_clave(self):
        clave=uuid.uuid4();v1=self.registrar(clave=clave)
        self.assertEqual(self.registrar(clave=clave).pk,v1.pk);self.assertEqual(ToleranciaCalibracion.objects.count(),1)
        with self.assertRaises(ValidationError): self.registrar(clave=clave,backlash_max='0.30')
        self.assertEqual(ToleranciaCalibracion.objects.count(),1)

    def test_validaciones_sin_inventar_valores(self):
        for malo in (dict(backlash_min=None),dict(backlash_max=''),dict(backlash_min='0.30',backlash_max='0.10'),dict(marca=''),dict(ratio=' '),dict(rotacion=''),dict(fuente=' '),
                dict(unidad_longitud=''),dict(unidad_longitud='cm'),dict(backlash_min='-0.1'),dict(tolerancia='-1'),dict(calibracion_min='0.2',calibracion_max='0.1'),
                dict(calibracion_min='0.1',calibracion_max=None),dict(unidad_presion=''),dict(mediciones_criterio=''),dict(mediciones_parametro='',mediciones_criterio='Solo criterio'),
                dict(mediciones_parametro='PRESION'),dict(mediciones_parametro='CALIBRACION',calibracion_min=None,calibracion_max=None),dict(presion_vertical_max=None),dict(backlash_min='0.1234567'),dict(tipo_equipo='OTRO')):
            with self.subTest(malo=malo),self.assertRaises(ValidationError): self.registrar(**malo)
        with self.assertRaises(ValidationError): s.registrar_tolerancia(usuario=self.oficina,empresa=self.empresa,clave=uuid.uuid4(),datos=dict(self.datos(),autor_id=1))
        self.assertFalse(ToleranciaCalibracion.objects.exists())
        v=self.registrar(presion_vertical_min=None,presion_vertical_max=None,presion_horizontal_min=None,presion_horizontal_max=None,unidad_presion='')   # las presiones solo cuando aplican
        self.assertEqual(v.unidad_presion,'')

    def test_versiones_inmutables_en_aplicacion_y_en_base_de_datos(self):
        v=self.registrar()
        v.fuente='Otra';
        with self.assertRaises(PermissionDenied): v.save()
        with self.assertRaises(PermissionDenied),transaction.atomic(): v.delete()
        with self.assertRaises(DatabaseError),transaction.atomic(): ToleranciaCalibracion.objects.filter(pk=v.pk).update(backlash_max='9')
        with self.assertRaises(DatabaseError),transaction.atomic(): ToleranciaCalibracion.objects.filter(pk=v.pk)._raw_delete(ToleranciaCalibracion.objects.db)
        with self.assertRaises(DatabaseError),transaction.atomic():
            ToleranciaCalibracion.objects.create(empresa=self.empresa,tipo_equipo='REDUCTOR',marca='A',modelo='B',ratio='1',rotacion='2',configuracion='x',version=1,
                unidad_longitud='mm',fuente='f',huella='h',autor=self.oficina,backlash_min='0.3',backlash_max='0.1')   # el rango invertido lo rechaza la restricción
        v.refresh_from_db();self.assertEqual(v.fuente,'Manual FICTICIO QA, sección 4')

    def test_retirar_crea_version_inactiva_con_motivo(self):
        v1=self.registrar()
        with self.assertRaises(ValidationError): s.retirar_tolerancia(usuario=self.oficina,empresa=self.empresa,clave=uuid.uuid4(),tolerancia_id=v1.pk,motivo=' ')
        with self.assertRaises(PermissionDenied): s.retirar_tolerancia(usuario=self.tecnico,empresa=self.empresa,clave=uuid.uuid4(),tolerancia_id=v1.pk,motivo='x')
        v2=s.retirar_tolerancia(usuario=self.admin,empresa=self.empresa,clave=uuid.uuid4(),tolerancia_id=v1.pk,motivo='Manual reemplazado')
        self.assertEqual((v2.version,v2.activa,v2.sustituye_id,v2.motivo),(2,False,v1.pk,'Manual reemplazado'));self.assertIsNone(s.tolerancia_vigente(self.empresa,self.equipo))
        with self.assertRaises(ValidationError): s.retirar_tolerancia(usuario=self.admin,empresa=self.empresa,clave=uuid.uuid4(),tolerancia_id=v1.pk,motivo='otra vez')
        v3=self.registrar(motivo='Se reactivan con nuevos límites');self.assertEqual((v3.version,v3.activa),(3,True))
        self.assertEqual(Auditoria.objects.filter(empresa=self.empresa,accion='CAL_TOLERANCIA_RETIRADA').count(),1)

    # ------------------------------------------------------------------ comparación automática en la hoja de TALLER
    def test_taller_genera_su_hoja_y_las_16_mediciones_se_comparan_con_los_limites(self):
        v1=self.registrar()
        c,em=self.hoja();snap=em.snapshot
        self.assertEqual((snap['verificacion']['estado'],snap['verificacion']['version']),('TOLERANCIA_CONFIGURADA',1))
        evaluaciones=[r['evaluacion'] for r in snap['mediciones']]   # 0.11 … 0.26 frente a un backlash de 0.10 a 0.20 mm
        self.assertEqual(evaluaciones,['Dentro del rango configurado']*10+['Fuera del rango configurado']*6)
        comparaciones={x['parametro']:x['evaluacion'] for x in snap['verificacion']['comparaciones']}
        self.assertEqual(comparaciones,{'Backlash':'Dentro del rango configurado','Calibración total':'Fuera del rango configurado','Presión eje vertical':'Dentro del rango configurado',
            'Presión eje horizontal':'Fuera del rango configurado','Tolerancia de calibración':'Coincide con la tolerancia configurada'})
        self.assertTrue(any(a.startswith('Fuera de lo configurado') and 'Calibración total 0.1 mm' in a and 'Presión eje horizontal 40 psi' in a for a in snap['advertencias']))
        self.assertEqual(c.estado,'FINALIZADA')
        self.assertFalse(DecisionCalibracion.objects.filter(calibracion=c).exists())   # sin aprobación adicional
        texto=self.texto_pdf(em)
        self.assertEqual((texto.count('Dentro del rango configurado'),texto.count('Fuera del rango configurado')),(10,6))
        self.assertIn('no sustituye la valoración técnica',texto.replace('\n',' '))
        self.assertNotIn('Sin tolerancia definida',texto)
        # Los límites quedan congelados con la hoja: una versión nueva o el retiro no cambian la hoja generada.
        self.registrar(backlash_max='0.50',motivo='Ajuste posterior');c.refresh_from_db()
        self.assertEqual((c.parametros['_tolerancias']['version'],c.parametros['_tolerancias']['tolerancia']),(1,v1.pk))
        self.assertEqual([r['evaluacion'] for r in s.snapshot(c)['mediciones']],evaluaciones)
        self.registrar(mediciones_parametro='',mediciones_criterio='',motivo='Se retira la correspondencia');self.assertEqual([r['evaluacion'] for r in s.snapshot(c)['mediciones']],evaluaciones)   # la hoja conserva lo congelado

    def test_sin_correspondencia_documentada_no_se_declara_conforme_ni_no_conforme(self):
        self.registrar(mediciones_parametro='',mediciones_criterio='')   # límites configurados, pero sin documentar qué miden las 16 mediciones
        c,em=self.hoja();snap=em.snapshot
        self.assertEqual([r['evaluacion'] for r in snap['mediciones']],['Sin criterio de evaluación definido']*16)
        self.assertTrue(all((r['minimo'],r['maximo'])==(None,None) for r in snap['mediciones']))
        self.assertEqual({x['parametro']:x['evaluacion'] for x in snap['verificacion']['comparaciones']}['Backlash'],'Dentro del rango configurado')   # los parámetros con su propio límite sí se comparan
        texto=self.texto_pdf(em);self.assertEqual(texto.count('Sin criterio de evaluación definido'),17)   # 16 mediciones + el aviso
        self.assertEqual((texto.count('Dentro del rango configurado'),texto.count('Fuera del rango configurado')),(0,0))
        self.assertNotIn('Conforme',texto)

    def test_mediciones_correspondientes_a_la_calibracion_se_comparan_solo_con_su_rango_y_unidad(self):
        self.registrar(unidad_longitud='µm',backlash_min='100',backlash_max='200',tolerancia='50',calibracion_min='50',calibracion_max='260',mediciones_parametro='CALIBRACION')
        c,em=self.hoja();ev=[r['evaluacion'] for r in em.snapshot['mediciones']]   # 0.05 a 0.26 mm
        self.assertEqual(ev,['Dentro del rango configurado']*16)
        self.assertEqual((em.snapshot['mediciones'][0]['minimo'],em.snapshot['mediciones'][0]['maximo']),('0.05','0.26'))
        self.assertIn('calibración configurado',em.snapshot['verificacion']['texto'])

    def test_taller_consulta_los_limites_aplicables_pero_no_los_configura(self):
        self.registrar();taller=Client();taller.force_login(self.tecnico);c=self.abrir()
        html=taller.get(reverse('masiscam:calibracion_revision',args=[c.pk])).content.decode()
        self.assertIn('data-tolerancia-configurada',html);self.assertIn('solo consulta',html);self.assertIn('backlash 0.1 a 0.2 mm',html)
        self.assertNotIn('data-form-tolerancia',html);self.assertEqual(taller.get(reverse('masiscam:calibracion_tolerancias')).status_code,403)
        self.assertNotIn('Tolerancias de calibración',taller.get(reverse('masiscam:taller_inicio')).content.decode().split('<main')[0])   # sin entrada de menú

    def test_menu_de_oficina_y_administrador_incluye_tolerancias(self):
        for usuario,inicio in ((self.oficina,'oficina_inicio'),(self.admin,'dashboard')):
            cliente=Client();cliente.force_login(usuario);cabecera=cliente.get(reverse('masiscam:'+inicio)).content.decode().split('<main')[0]
            self.assertIn(f'href="{reverse("masiscam:calibracion_tolerancias")}">Tolerancias de calibración</a>',cabecera)

    def test_limites_en_otra_unidad_se_convierten_sin_inventar_equivalencias(self):
        self.registrar(unidad_longitud='µm',backlash_min='100',backlash_max='200',tolerancia='50',calibracion_min='50',calibracion_max='80')   # 0.10 a 0.20 mm
        c,em=self.hoja();ev=[r['evaluacion'] for r in em.snapshot['mediciones']]
        self.assertEqual(ev,['Dentro del rango configurado']*10+['Fuera del rango configurado']*6)
        self.assertEqual((em.snapshot['mediciones'][0]['minimo'],em.snapshot['mediciones'][0]['maximo'],em.snapshot['mediciones'][0]['unidad']),('0.1','0.2','mm'))

    def test_sin_limites_muestra_sin_tolerancia_definida_y_no_declara_conformidad(self):
        c,em=self.hoja();snap=em.snapshot
        self.assertTrue(all(r['evaluacion']=='Sin tolerancia definida: no se evalúa' for r in snap['mediciones']));self.assertNotEqual(snap['verificacion']['estado'],'TOLERANCIA_CONFIGURADA')
        texto=self.texto_pdf(em);self.assertIn('Sin tolerancia definida',texto);self.assertNotIn('Dentro del rango',texto);self.assertIn('No se afirma conformidad técnica',texto.replace('\n',' '))

    def test_limites_retirados_antes_de_generar_vuelven_a_sin_tolerancia_definida(self):
        v=self.registrar();s.retirar_tolerancia(usuario=self.oficina,empresa=self.empresa,clave=uuid.uuid4(),tolerancia_id=v.pk,motivo='Fuente no vigente')
        c,em=self.hoja();self.assertTrue(all(r['evaluacion']=='Sin tolerancia definida: no se evalúa' for r in em.snapshot['mediciones']))
        self.assertIsNone(c.parametros['_tolerancias'])

    def test_otra_configuracion_o_empresa_no_hereda_limites(self):
        self.registrar(rotacion='Antihoraria')   # otra rotación: no coincide con el equipo
        self.assertIsNone(s.tolerancia_vigente(self.empresa,self.equipo))
        c,em=self.hoja();self.assertTrue(all(r['evaluacion'].startswith('Sin tolerancia definida') for r in em.snapshot['mediciones']))

    # ------------------------------------------------------------------ pantalla de configuración
    def test_pantalla_para_oficina_y_administrador_y_no_para_taller(self):
        url=reverse('masiscam:calibracion_tolerancias')
        taller=Client();taller.force_login(self.tecnico);self.assertEqual(taller.get(url).status_code,403);self.assertEqual(taller.post(url,dict(op='guardar')).status_code,403)
        oficina=Client();oficina.force_login(self.oficina);html=oficina.get(url).content.decode()
        self.assertIn('data-form-tolerancia',html);self.assertIn('Sin tolerancia definida',html);self.assertNotIn('data-fila-tolerancia',html)
        datos=dict(self.datos(),clave=uuid.uuid4(),op='guardar');respuesta=oficina.post(url,datos);self.assertEqual(respuesta.status_code,302)
        html=oficina.get(respuesta['Location']).content.decode();self.assertIn('data-guardado',html);self.assertIn('data-fila-tolerancia',html);self.assertIn('Manual FICTICIO QA',html)
        malo=oficina.post(url,dict(datos,clave=uuid.uuid4(),backlash_max='0.01'));self.assertEqual(malo.status_code,400);self.assertIn('mínimo no puede ser mayor',malo.content.decode())
        self.assertEqual(ToleranciaCalibracion.objects.count(),1)
        admin=Client();admin.force_login(self.admin);v=ToleranciaCalibracion.objects.get()
        self.assertEqual(admin.post(url,dict(op='retirar',clave=uuid.uuid4(),tolerancia_id=v.pk,motivo='')).status_code,400)
        self.assertEqual(admin.post(url,dict(op='retirar',clave=uuid.uuid4(),tolerancia_id=v.pk,motivo='Fuente reemplazada')).status_code,302)
        self.assertEqual(ToleranciaCalibracion.objects.count(),2);html=admin.get(url+f'?historial={v.pk}').content.decode();self.assertIn('data-historial-tolerancia',html);self.assertIn('Retirada',html)
        self.assertIn(f'value="{v.marca}"',admin.get(url+f'?base={v.pk}').content.decode())   # nueva versión precargada desde la existente
        self.assertIn('data-enlace-tolerancias',admin.get(reverse('masiscam:calibracion_inicio')).content.decode())
        self.assertIn('data-enlace-tolerancias',oficina.get(reverse('masiscam:oficina_inicio')).content.decode())   # OFICINA llega desde su inicio


@skipUnless(os.environ.get('MASISCAM_BROWSER_TESTS')=='1','Navegador aislado explícito')
@override_settings(**FLAGS)
class ToleranciasBrowserTests(ToleranciasBase,StaticLiveServerTestCase):
    def _galleta(self,usuario):
        client=Client();client.force_login(usuario)
        return client.cookies[settings.SESSION_COOKIE_NAME].value

    def test_oficina_configura_y_taller_genera_su_hoja_comparada_en_escritorio_y_movil(self):
        from playwright.sync_api import sync_playwright,expect
        from .test_calibracion import VALORES_HOJA
        d=self.datos(fuente='Manual FICTICIO QA, sección 4')
        pantalla=self.live_server_url+reverse('masiscam:calibracion_tolerancias')
        c=self.abrir();hoja=self.live_server_url+reverse('masiscam:calibracion_revision',args=[c.pk])
        oficina,taller=self._galleta(self.oficina),self._galleta(self.tecnico)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for ancho in (390,1440):
                    contexto=browser.new_context(viewport=dict(width=ancho,height=1000))
                    contexto.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=oficina,url=self.live_server_url)])
                    page=contexto.new_page();page.goto(pantalla)
                    expect(page.locator('h1')).to_have_text('Tolerancias de calibración');expect(page.locator('[data-sin-tolerancias]')).to_be_visible()
                    if ancho==1440:
                        for campo in ('marca','modelo','ratio','rotacion','backlash_min','backlash_max','tolerancia','calibracion_min','calibracion_max','presion_vertical_min',
                                'presion_vertical_max','presion_horizontal_min','presion_horizontal_max','fuente'): page.locator(f'#id_{campo}').fill(str(d[campo]))
                        page.locator('#id_unidad_longitud').select_option('mm');page.locator('#id_unidad_presion').select_option('psi')
                        page.locator('#id_mediciones_parametro').select_option('BACKLASH');page.locator('#id_mediciones_criterio').fill(d['mediciones_criterio'])
                        page.get_by_role('button',name='Guardar límites').click()
                        expect(page.locator('[data-guardado]')).to_be_visible();expect(page.locator('[data-fila-tolerancia]')).to_contain_text('v1')
                    else:
                        expect(page.get_by_role('button',name='Guardar límites')).to_be_visible()
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),f'la pantalla desborda a {ancho}px')
                    contexto.close()
                for ancho in (390,1440):
                    contexto=browser.new_context(viewport=dict(width=ancho,height=1000))
                    contexto.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=taller,url=self.live_server_url)])
                    page=contexto.new_page();page.goto(hoja)
                    expect(page.locator('[data-tolerancia-configurada]')).to_contain_text('Límites aplicables a esta configuración (versión 1')
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),f'la hoja desborda a {ancho}px')
                    if ancho==1440:
                        page.locator('#id_unidad_longitud').select_option('mm');page.locator('#id_unidad_presion').select_option('psi')
                        for n,codigo in enumerate(('SHIM-VS','SHIM-VI','SHIM-H','BACKLASH','TOLERANCIA','CAL-TOTAL','MINIMO','MAXIMO','PRES-V','PRES-H')):
                            page.locator(f'#id_param_{n}').fill(VALORES_HOJA[codigo])
                        for n in range(16): page.locator(f'#id_medicion_{n}').fill(str(Decimal('0.11')+Decimal(n)/100))
                        page.get_by_role('button',name='Generar hoja de calibración').click()
                        expect(page.locator('[data-estado-hoja]')).to_have_text('Finalizada')
                    contexto.close()
            finally: browser.close()
        em=EmisionCalibracion.objects.get(calibracion=c,estado='GENERADA');snap=em.snapshot
        self.assertEqual(snap['verificacion']['estado'],'TOLERANCIA_CONFIGURADA')
        self.assertEqual([r['evaluacion'] for r in snap['mediciones']],['Dentro del rango configurado']*10+['Fuera del rango configurado']*6)
        self.assertFalse(DecisionCalibracion.objects.filter(calibracion=c).exists())   # TALLER la generó sin aprobación adicional
        texto=self.texto_pdf(em);self.assertEqual((texto.count('Dentro del rango configurado'),texto.count('Fuera del rango configurado')),(10,6))

    def test_menu_de_oficina_y_administrador_abre_tolerancias_en_escritorio_y_movil(self):
        from playwright.sync_api import sync_playwright,expect
        self.registrar(mediciones_parametro='',mediciones_criterio='')   # sin correspondencia: la pantalla debe decirlo
        galletas={u.username:self._galleta(u) for u in (self.oficina,self.admin)}
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for usuario,inicio in ((self.oficina,'oficina_inicio'),(self.admin,'dashboard')):
                    galleta=galletas[usuario.username]
                    for ancho in (390,1440):
                        contexto=browser.new_context(viewport=dict(width=ancho,height=1000))
                        contexto.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=galleta,url=self.live_server_url)])
                        page=contexto.new_page();page.goto(self.live_server_url+reverse('masiscam:'+inicio))
                        if ancho==390: page.locator('.app-menu-toggle').click()
                        enlace=page.locator('#appMenu').get_by_role('link',name='Tolerancias de calibración');expect(enlace).to_be_visible();enlace.click()
                        expect(page.locator('h1')).to_have_text('Tolerancias de calibración')
                        expect(page.locator('[data-fila-tolerancia]')).to_contain_text('sin criterio de evaluación definido')
                        expect(page.locator('#id_mediciones_parametro')).to_be_visible();expect(page.get_by_role('button',name='Guardar límites')).to_be_visible()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),f'{usuario.username}: la pantalla desborda a {ancho}px')
                        contexto.close()
            finally: browser.close()
