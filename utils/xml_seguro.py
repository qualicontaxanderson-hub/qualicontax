# -*- coding: utf-8 -*-
"""Leitura de XML resistente a XXE (XML External Entity) — usada na porta de
entrada do Q-Robô, onde o XML vem de fora (pista do cliente).

Prefere defusedxml (se instalado); senão, cai no ElementTree do stdlib DEPOIS de
barrar qualquer DTD/entidade. O expat do ElementTree NÃO resolve entidade externa
por padrão (não busca URL), então barrar o ``<!DOCTYPE>``/``<!ENTITY>`` fecha também
a expansão de entidades internas (billion laughs). NF-e/NFC-e nunca traz DTD, então
recusar é seguro."""
import xml.etree.ElementTree as ET

try:                                    # defusedxml é a defesa preferida (se houver)
    from defusedxml.ElementTree import fromstring as _defused_fromstring
except Exception:                       # pragma: no cover
    _defused_fromstring = None


class XmlInseguroError(ValueError):
    """XML com DTD/entidade — recusado por proteção anti-XXE."""


def _barrar_dtd(xml_bytes):
    low = (xml_bytes if isinstance(xml_bytes, (bytes, bytearray)) else xml_bytes.encode(
        'utf-8', 'replace')).lower()
    if b'<!doctype' in low or b'<!entity' in low:
        raise XmlInseguroError('XML com DTD/ENTITY não é permitido (proteção XXE).')


def fromstring_seguro(xml_bytes):
    """Parseia ``xml_bytes`` (bytes/str) recusando DTD/entidades. Devolve o Element
    raiz. Levanta XmlInseguroError (subclasse de ValueError) se houver DTD/ENTITY, ou
    ValueError/ParseError se o XML for inválido."""
    if _defused_fromstring is not None:
        # defusedxml já recusa DTD/entidade por padrão (forbid_dtd/forbid_entities).
        return _defused_fromstring(xml_bytes)
    _barrar_dtd(xml_bytes)
    return ET.fromstring(xml_bytes)
