# 🆘 GUIA RÁPIDO: Como Executar a Migração CNAE

**Tempo:** 5 minutos  
**Urgência:** MÉDIA (sistema funciona, mas CNAE desabilitado)

---

## 📍 Você Está Aqui

```
✅ FEITO: Correção emergencial deployada
⏳ AGORA: Executar migração do banco
📋 DEPOIS: Re-ativar CNAE no código
```

---

## 🎯 O Que Você Precisa Fazer AGORA

### Passo 1: Executar Migração (ESCOLHA UMA OPÇÃO)

#### OPÇÃO A: Railway CLI (Mais Fácil)
```bash
railway run mysql -u root < migrations/add_cnae_fields.sql
```

#### OPÇÃO B: Copiar e Colar no MySQL
1. Abrir Railway → Database → Query
2. Copiar isto:
```sql
ALTER TABLE clientes 
ADD COLUMN IF NOT EXISTS cnae_fiscal VARCHAR(10) AFTER porte_empresa,
ADD COLUMN IF NOT EXISTS cnae_fiscal_descricao VARCHAR(500) AFTER cnae_fiscal;

CREATE INDEX IF NOT EXISTS idx_cnae_fiscal ON clientes(cnae_fiscal);
```
3. Colar e executar

### Passo 2: Verificar
```sql
DESCRIBE clientes;
```

✅ Deve mostrar `cnae_fiscal` e `cnae_fiscal_descricao`

---

## 📧 Se Não Conseguir

**Envie mensagem com:**
1. Screenshot do erro (se houver)
2. Resultado do `DESCRIBE clientes`
3. Qual opção tentou (A ou B)

---

## ✅ Depois da Migração

Avise para:
1. Descomentar linhas CNAE no código
2. Fazer deploy final
3. Testar

---

**Leia completo:** `EMERGENCIA_CNAE_RESOLVIDA.md`
