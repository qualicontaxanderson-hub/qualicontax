"""Utilitários de Certificado Digital (.pfx / PKCS#12).

- Cifra/decifra a senha do certificado com Fernet (chave em ``DFE_CRYPTO_KEY``).
- Valida a senha abrindo o PKCS#12 e extrai o documento (CNPJ/CPF) do titular
  a partir do certificado ICP-Brasil.

A chave Fernet é própria do Qualicontax (independente do NH); senhas cifradas
aqui não são interoperáveis com outros sistemas.
"""
import os
import re
import logging

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography import x509
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

# OIDs ICP-Brasil onde o documento do titular fica no subjectAltName/otherName.
OID_CNPJ = '2.16.76.1.3.3'  # e-CNPJ: contém o CNPJ (14 dígitos)
OID_CPF = '2.16.76.1.3.1'   # e-CPF: contém nascimento(8) + CPF(11) + ...


class CertificadoError(Exception):
    """Erro genérico ao processar o certificado."""


class SenhaInvalidaError(CertificadoError):
    """Senha do certificado incorreta."""


class DocumentoNaoEncontradoError(CertificadoError):
    """Não foi possível extrair CNPJ/CPF do certificado."""


# ---------------------------------------------------------------------------
# Criptografia da senha (Fernet)
# ---------------------------------------------------------------------------
def _crypto_key() -> bytes:
    key = (os.getenv('DFE_CRYPTO_KEY', '') or '').strip()
    if not key:
        raise CertificadoError('DFE_CRYPTO_KEY não configurada no ambiente.')
    return key.encode()


def cifrar_senha(senha: str) -> bytes:
    """Cifra a senha do certificado e retorna o token Fernet (bytes)."""
    return Fernet(_crypto_key()).encrypt((senha or '').encode())


def decifrar_senha(token) -> str:
    """Decifra o token Fernet e retorna a senha em texto puro."""
    if isinstance(token, str):
        token = token.encode()
    try:
        return Fernet(_crypto_key()).decrypt(token).decode()
    except InvalidToken as exc:
        raise CertificadoError('Falha ao decifrar a senha (chave inválida?).') from exc


# ---------------------------------------------------------------------------
# Leitura do PKCS#12 (.pfx)
# ---------------------------------------------------------------------------
def _san_otherName_digitos(cert, oid):
    """Dígitos do SAN otherName do OID dado (ICP-Brasil), PULANDO o tag+length
    DER do valor. Isso importa no e-CPF: se a length (2º byte) calhar de ser um
    ASCII '0'-'9', ela injetaria um dígito e deslocaria as posições do CPF.

    Alinhado ao parser do NH (nh-transportes), provado em produção.
    """
    try:
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
    except Exception:
        return None
    for gn in san:
        if isinstance(gn, x509.OtherName) and gn.type_id.dotted_string == oid:
            raw = gn.value  # DER do valor (bytes): [tag][length][conteudo]
            if isinstance(raw, (bytes, bytearray)) and len(raw) >= 2 and raw[1] < 0x80:
                conteudo = raw[2:]          # pula tag+length (forma curta)
            else:
                conteudo = raw
            texto = conteudo.decode('latin-1', errors='ignore')
            return re.sub(r'\D', '', texto)
    return None


def _extrair_documento(cert):
    """Extrai ``(documento, tipo_doc)`` do certificado ICP-Brasil:
      - e-CNPJ (OID 2.16.76.1.3.3): 14 dígitos            -> ('<14>', 'CNPJ')
      - e-CPF  (OID 2.16.76.1.3.1): nascimento[8]+CPF[11] -> ('<11>', 'CPF')
        (o CPF são os 11 dígitos APÓS os 8 da data de nascimento -> posições 8:19)
    Fallback: CN 'RAZÃO:CNPJ'. Retorna ``(None, None)`` se não achar.

    Alinhado ao parser do NH (nh-transportes), provado em produção.
    """
    # 1) e-CNPJ
    d = _san_otherName_digitos(cert, OID_CNPJ)
    if d and len(d) >= 14:
        return d[:14], 'CNPJ'
    # 2) e-CPF (CPF = 11 dígitos após os 8 da data de nascimento)
    d = _san_otherName_digitos(cert, OID_CPF)
    if d and len(d) >= 19:
        return d[8:19], 'CPF'
    # 3) Fallback: Common Name "NOME:CNPJ"
    try:
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        if ':' in cn:
            digitos = re.sub(r'\D', '', cn.split(':')[-1])
            if len(digitos) >= 14:
                return digitos[:14], 'CNPJ'
    except Exception:
        pass
    return None, None


def abrir_certificado(pfx_bytes: bytes, senha: str) -> dict:
    """Valida a senha do .pfx e extrai os dados do titular.

    Returns:
        dict com ``documento`` (só dígitos), ``tipo_doc`` (``'CNPJ'``/``'CPF'``),
        ``validade`` (date ou None) e ``titular`` (CN do certificado).

    Raises:
        SenhaInvalidaError: senha incorreta.
        DocumentoNaoEncontradoError: sem CNPJ/CPF legível.
        CertificadoError: arquivo inválido/corrompido.
    """
    try:
        _key, cert, _extra = pkcs12.load_key_and_certificates(
            pfx_bytes, (senha or '').encode())
    except ValueError as exc:
        msg = str(exc).lower()
        if any(k in msg for k in ('mac', 'invalid', 'decrypt', 'password')):
            raise SenhaInvalidaError('Senha do certificado incorreta.') from exc
        raise CertificadoError(f'Arquivo de certificado inválido: {exc}') from exc

    if cert is None:
        raise CertificadoError('Certificado sem chave/certificado utilizável.')

    documento, tipo_doc = _extrair_documento(cert)
    if not documento:
        raise DocumentoNaoEncontradoError(
            'Não foi possível identificar o CNPJ/CPF do titular do certificado.')

    try:
        na = getattr(cert, 'not_valid_after_utc', None) or cert.not_valid_after
        validade = na.date()
    except Exception:
        validade = None

    titular = ''
    cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if cns:
        titular = cns[0].value

    return {
        'documento': documento,
        'tipo_doc': tipo_doc,
        'validade': validade,
        'titular': titular,
    }
