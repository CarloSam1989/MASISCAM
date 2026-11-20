"""Campos de formulario compartidos."""
import re

from django import forms


def normalizar_decimal(valor):
    """Convierte «1,5», «1.5», «1.234,50» o «1,234.50» a notación con punto.

    Un único separador es decimal. Varios iguales (o el que precede al otro) son de miles y solo se
    aceptan en grupos de tres cifras. Lo que no encaja se devuelve sin cambios para que el campo lo rechace.
    """
    if not isinstance(valor, str):
        return valor
    texto = re.sub(r'[\s ]', '', valor)
    m = re.fullmatch(r'([+-]?)(\d[\d.,]*|[.,]\d+)', texto)
    if not m:
        return texto
    signo, numero = m.groups()
    if ',' in numero and '.' in numero:
        decimal = ',' if numero.rfind(',') > numero.rfind('.') else '.'
        miles = '.' if decimal == ',' else ','
        entero, _, fraccion = numero.rpartition(decimal)
        if not fraccion.isdigit() or not re.fullmatch(r'\d{1,3}(?:%s\d{3})*' % re.escape(miles), entero):
            return texto
        return f"{signo}{entero.replace(miles, '')}.{fraccion}"
    for separador in ',.':
        if numero.count(separador) > 1:
            if not re.fullmatch(r'\d{1,3}(?:%s\d{3})+' % re.escape(separador), numero):
                return texto
            return signo + numero.replace(separador, '')
    return signo + numero.replace(',', '.')


class DecimalFlexible(forms.DecimalField):
    """DecimalField que admite coma o punto decimal sin depender de la configuración regional."""

    def to_python(self, value):
        return super().to_python(normalizar_decimal(value))
