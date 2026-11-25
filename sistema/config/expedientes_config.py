"""Explicit deployment configuration; enabling code never grants approvals."""
import json
import logging
import re
from pathlib import Path
from datetime import datetime
from django.core.exceptions import ImproperlyConfigured


def _read(path,expected):
    if not path: return None
    def unique_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError('duplicate key')
            result[key]=value
        return result
    try:
        source=Path(path)
        if not source.is_absolute() or source.stat().st_size>1024*1024: raise ValueError('invalid source')
        data=json.loads(source.read_text(encoding='utf-8-sig'),object_pairs_hook=unique_pairs)
        if not isinstance(data,expected): raise ValueError('invalid structure')
        return data
    except (OSError,ValueError,TypeError):
        raise ImproperlyConfigured('Archivo de aprobaciones MASISCAM inválido; no se activa con valores sustitutos.') from None


def _politica_historica(path):
    """Política comercial JSON: solo sirve para validar aceptaciones históricas del cliente (opcional).

    La autorización vigente es la aprobación conjunta de ADMINISTRADOR, así que una variable ausente, vacía,
    obsoleta o inválida NUNCA impide el arranque: se ignora (sin política) y se deja constancia en el registro.
    Los archivos de protocolos de calibración conservan su validación estricta."""
    if not path: return None
    try:
        policy=_read(path,dict)
        required={'version','aprobacion_propietario','procedimiento','vigente_desde'}
        if set(policy)!=required or any(not isinstance(v,str) or not v.strip() for v in policy.values()): raise ValueError
        if datetime.fromisoformat(policy['vigente_desde']).utcoffset() is None: raise ValueError
        return policy
    except (ImproperlyConfigured,ValueError,TypeError,KeyError):
        logging.getLogger('masiscam.config').warning(
            'MASISCAM_POLITICA_COMERCIAL_FILE ignorado (ausente, obsoleto o inválido): la autorización comercial es '
            'la aprobación conjunta de ADMINISTRADOR; solo se omite la validación de aceptaciones históricas.')
        return None


def configurar(env,debug):
    enabled=env.bool('MASISCAM_EXPEDIENTES_ENABLED',default=False)
    result={f'MASISCAM_{phase}_LOCAL':enabled or env.bool(f'MASISCAM_{phase}_ENABLED',default=False)
        or (debug and env.bool(f'MASISCAM_{phase}_LOCAL',default=False))
        for phase in ('RECEPCION','TECNICA','OPERACION','CALIBRACION','CIERRE')}
    policy=_politica_historica(env('MASISCAM_POLITICA_COMERCIAL_FILE',default=''))
    protocols=_read(env('MASISCAM_PROTOCOLOS_CALIBRACION_FILE',default=''),list) or []
    for anchor in protocols:
        if (not isinstance(anchor,dict) or set(anchor)!={'huella','aprobacion_propietario'}
                or not isinstance(anchor.get('huella'),str) or not re.fullmatch('[0-9a-f]{64}',anchor['huella'])
                or not isinstance(anchor.get('aprobacion_propietario'),str) or not anchor['aprobacion_propietario'].strip()):
            raise ImproperlyConfigured('Cada protocolo exige su huella exacta y referencia de aprobación expresa.')
    if len({p['huella'] for p in protocols})!=len(protocols):
        raise ImproperlyConfigured('Huella de protocolo duplicada.')
    result.update(MASISCAM_POLITICA_COMERCIAL_LOCAL=policy,MASISCAM_PROTOCOLOS_CALIBRACION_LOCAL=protocols)
    return result
