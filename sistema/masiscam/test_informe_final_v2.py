"""Informe final v2: hoja de calibración incorporada sin cambios, anexos visuales numerados, histórico separado y versiones anteriores intactas. Solo QA sintético."""
import hashlib
import uuid
from io import BytesIO
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase,override_settings
from django.utils import timezone
from PIL import Image
from pypdf import PdfReader
from .models import *
from . import cierre_services as cs,calibracion_services as cal,operacion_services as o
from .test_calibracion import HojaBase
from .test_cierre import FLAGS
from .test_operacion import repuesto_datos


def imagen(color,nombre='foto.png',medidas=(800,600)):
    flujo=BytesIO();Image.new('RGB',medidas,color).save(flujo,'PNG')
    return SimpleUploadedFile(nombre,flujo.getvalue(),content_type='image/png')


def leer(documento):
    with documento.archivo.open('rb') as f: return f.read()


@override_settings(**FLAGS)
class InformeFinalV2Tests(HojaBase,TestCase):
    def preparar_informe(self,historico=False):
        # Segunda actividad de mantenimiento, con su fotografía (la actividad de la preparación ya está cerrada).
        act=o.iniciar_actividad(usuario=self.tecnico,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),
            datos=dict(trabajo='Montaje de retén FICTICIO QA',componentes='Retén',inicio=timezone.now()-timezone.timedelta(hours=2)))
        o.evidencia_actividad(usuario=self.tecnico,empresa=self.empresa,actividad_id=act.pk,clave=uuid.uuid4(),clase='DURANTE',
            leyenda='Montaje del retén nuevo',archivo=imagen('#33aa55','mantenimiento.png'))
        o.terminar_actividad(usuario=self.tecnico,empresa=self.empresa,actividad_id=act.pk,clave=uuid.uuid4(),
            datos=dict(fin=timezone.now()-timezone.timedelta(hours=1),trabajo_realizado='Montaje ficticio QA',resultado='Sin resultado industrial'))
        c=self.abrir();self.llenar(c);c.refresh_from_db()
        cal.evidencia(usuario=self.tecnico,empresa=self.empresa,calibracion_id=c.pk,version=c.version_edicion,clave=uuid.uuid4(),clase='FOTO',
            leyenda='Fotografía de la calibración',archivo=imagen('#aa3333','calibracion.png'))
        self.finalizar(c);self.hoja=self.generar(c)
        if historico:
            o.recibir_repuesto(usuario=self.oficina,empresa=self.empresa,expediente_id=self.exp.pk,clave=uuid.uuid4(),datos=repuesto_datos(),
                fotografia=imagen('#5533aa','historico.png'))

    def emitir(self,**extra):
        em=cs.reservar(usuario=self.oficina,**self.base,clave=uuid.uuid4(),**extra);cs.generar(em.pk);em.refresh_from_db();return em

    def test_pdf_completo_con_hoja_sin_cambios_anexos_numerados_y_trazabilidad(self):
        self.preparar_informe(historico=True)
        em=self.emitir();snap=em.snapshot
        self.assertEqual(snap['plantilla'],'final-v2');self.assertEqual(snap['calibracion_documento']['hash'],self.hoja.documento.hash_sha256)
        datos=leer(em.documento);self.assertEqual(hashlib.sha256(datos).hexdigest(),em.hash_sha256)
        lector=PdfReader(BytesIO(datos));paginas=len(lector.pages);textos=[p.extract_text() for p in lector.pages];todo='\n'.join(textos)
        # Portada, identificación, diagnóstico, cotización, mantenimiento, calibración y trazabilidad.
        for palabra in ('INFORME FINAL DE MANTENIMIENTO','IDENTIFICACIÓN DEL CLIENTE Y DEL EQUIPO','INSPECCIÓN, DIAGNÓSTICO Y PIEZAS','COTIZACIÓN Y AUTORIZACIÓN',
                'MANTENIMIENTO REALIZADO','HOJA DE CALIBRACIÓN (ELABORADA POR TALLER)','TRAZABILIDAD Y DOCUMENTOS','Contenido'): self.assertIn(palabra,todo)
        # Paginación continua «Página n de N» en todas las páginas, incluidas las de la hoja incorporada.
        for n,texto in enumerate(textos,1): self.assertIn(f'Página {n} de {paginas}',texto)
        # La hoja se incorpora sin cambios: mismo texto, mismas imágenes y mismo número de páginas que el documento original.
        original=PdfReader(BytesIO(leer(self.hoja.documento)));inicio=next(i for i,t in enumerate(textos) if 'DATOS GENERALES DE TRANSMISIÓN' in t)
        for i,pagina in enumerate(original.pages):
            self.assertIn(pagina.extract_text().strip(),textos[inicio+i]);self.assertEqual(len(lector.pages[inicio+i].images),len(pagina.images))
        self.assertEqual(hashlib.sha256(leer(self.hoja.documento)).hexdigest(),self.hoja.documento.hash_sha256)   # el documento de la hoja no se tocó
        # Anexos visuales: una sola vez cada fotografía de la intervención actual; la de la calibración ya va dentro de la hoja.
        evidencias=snap['evidencias'];self.assertEqual(len({e['hash'] for e in evidencias}),len(evidencias))
        self.assertEqual(len({a['documento'] for a in snap['anexos']}),len(snap['anexos']))
        visuales=[e for e in evidencias if e['foto']];self.assertTrue(visuales)
        self.assertTrue(all(e['origen']=='ACTUAL' for e in visuales));self.assertTrue(any(e['seccion']=='Mantenimiento' for e in visuales))
        self.assertFalse(any(e['seccion']=='Calibración' and e['foto'] for e in evidencias))
        final=inicio+len(original.pages);self.assertEqual(sum(len(p.images)-1 for p in lector.pages[final:]),len(visuales))   # cada página lleva además el logotipo
        anexos_finales=chr(10).join(textos[final:])
        for n in range(1,len(visuales)+1): self.assertEqual(anexos_finales.count(f'ANEXO VISUAL {n}'+chr(10)),1)
        self.assertNotIn(f'ANEXO VISUAL {len(visuales)+1}',anexos_finales)   # numeración sin saltos ni repeticiones; la de la hoja es propia
        # Trazabilidad: SHA-256 de la hoja y de cada fotografía.
        self.assertIn(self.hoja.documento.hash_sha256[:32],todo.replace('\n',''))
        for e in evidencias: self.assertIn(e['hash'][:32],todo.replace('\n',''))
        # Lo histórico va aparte, rotulado, sin imágenes y sin presentarse como operación actual.
        self.assertIn('ANTERIORES AL FLUJO SIMPLIFICADO',todo);self.assertIn('Recepción de repuestos (histórica)',todo)
        historicos=[e for e in evidencias if e['origen']=='HISTORICO'];self.assertTrue(historicos);self.assertFalse(any(e['foto'] for e in historicos))
        self.assertNotIn('Sin recepciones de repuestos registradas',todo)
        for hist in historicos: self.assertNotIn(hist['leyenda']+' | Documento',todo)
        bloque=todo[todo.index('ANTERIORES AL FLUJO SIMPLIFICADO'):]
        self.assertNotIn('DOCUMENTOS DE LA INTERVENCIÓN ACTUAL',bloque)   # lo actual se lista antes que lo histórico, nunca después
        self.assertEqual(lector.outline[0].title,'Informe final de mantenimiento')

    def test_sin_historico_ni_duplicados_el_informe_es_compacto(self):
        self.preparar_informe()
        em=self.emitir();paginas=PdfReader(BytesIO(leer(em.documento))).pages;k=len(PdfReader(BytesIO(leer(self.hoja.documento))).pages)
        inicio=next(i for i,p in enumerate(paginas) if 'DATOS GENERALES DE TRANSMISIÓN' in p.extract_text())
        texto=chr(10).join(p.extract_text() for i,p in enumerate(paginas) if not inicio<=i<inicio+k)   # fuera de la hoja, que lleva su propio anexo
        self.assertNotIn('ANTERIORES AL FLUJO SIMPLIFICADO',texto);self.assertNotIn('Recepción de repuestos (histórica)',texto)
        self.assertEqual(texto.count('Fotografía de la calibración'),1)   # una sola mención: en la tabla de evidencias, no como anexo repetido
        self.assertTrue(all(e['origen']=='ACTUAL' for e in em.snapshot['evidencias']))

    def test_informe_v1_historico_sigue_vigente_y_la_version_nueva_no_lo_sobrescribe(self):
        self.preparar_informe()
        original=cs.snapshot
        with patch.object(cs,'snapshot',lambda exp,formato=1:original(exp,1)):
            v1=self.emitir()
        self.assertEqual(v1.snapshot['plantilla'],'final-v1');cs.vigente(v1)   # sigue vigente con su propio formato
        bytes_v1=leer(v1.documento);hash_v1,huella_v1,doc_v1=v1.hash_sha256,v1.huella,v1.documento_id
        texto_v1='\n'.join(p.extract_text() for p in PdfReader(BytesIO(bytes_v1)).pages);self.assertIn('Hoja de calibración (elaborada por TALLER)',texto_v1)
        v2=self.emitir(nueva_version=True,motivo='Incorporar la hoja de calibración y los anexos visuales')
        self.assertEqual((v2.version,v2.sustituye_id,v2.snapshot['plantilla']),(2,v1.pk,'final-v2'));self.assertNotEqual(v2.documento_id,doc_v1)
        v1.refresh_from_db();self.assertEqual((v1.hash_sha256,v1.huella,v1.documento_id),(hash_v1,huella_v1,doc_v1))
        self.assertEqual(leer(v1.documento),bytes_v1);self.assertEqual(hashlib.sha256(bytes_v1).hexdigest(),hash_v1)   # el PDF histórico no cambia ni un byte
        with self.assertRaises(Exception): cs.vigente(v1)   # sustituido por una versión posterior
