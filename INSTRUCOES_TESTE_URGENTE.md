# 🚨 INSTRUÇÕES URGENTES DE TESTE - CNPJ Auto-preenchimento

## ⚠️ IMPORTANTE: Precisamos Identificar O Problema!

Você reportou que "ainda puxa só a razão social e nome fantasia".

Para identificar o problema EXATO, preciso que faça este teste:

## 📋 Passo a Passo (5 minutos)

### 1. Abrir a Página
- Acesse: https://app.qualicontax.com.br/clientes/novo
- **IMPORTANTE:** Pressione `Ctrl+F5` (ou `Cmd+Shift+R` no Mac) para limpar cache

### 2. Abrir Console do Navegador
- Pressione `F12` (ou clique direito → "Inspecionar")
- Clique na aba "Console"
- **DEIXE O CONSOLE ABERTO**

### 3. Consultar um CNPJ
- Digite um CNPJ válido (exemplo: `08.629.788/0001-14`)
- Clique em "Consultar CNPJ"
- Aguarde 2-3 segundos

### 4. O QUE DEVE APARECER

#### ✅ Se o Fix Está Funcionando:

Você verá no console:
```
🎯 DADOS RECEBIDOS! Preenchendo automaticamente...
=== INÍCIO DO PREENCHIMENTO AUTOMÁTICO ===
=== DADOS RECEBIDOS DA API (COMPLETOS) ===
{
  "razao_social": "...",
  "telefone": "...",
  "cep": "...",
  etc...
}
✅ Razão Social preenchida: ...
✅ Nome Fantasia preenchido: ...
✅ Telefone preenchido: ...
✅ CEP preenchido: ...
✅ Logradouro preenchido: ...
etc...
```

E verá um ALERT mostrando:
```
✅ Dados preenchidos com sucesso!

Total de campos preenchidos: 12

Campos preenchidos automaticamente:
✓ Razão Social
✓ Nome Fantasia
✓ Telefone
✓ CEP
✓ Logradouro
etc...
```

#### ❌ Se NÃO Está Funcionando:

Você pode ver:
```
❌ Campo telefone não encontrado no DOM!
❌ Campo cep não encontrado!
etc...
```

OU pode não ver NADA (o que significa que a função nem foi executada).

### 5. TIRE SCREENSHOTS

**POR FAVOR, tire 3 screenshots:**

1. **Screenshot 1:** Console COMPLETO (todos os logs)
2. **Screenshot 2:** Formulário mostrando quais campos foram preenchidos
3. **Screenshot 3:** Aba "Network" (Rede) do DevTools mostrando a requisição para `/api/consultar-cnpj/`

### 6. INFORMAÇÕES NECESSÁRIAS

Me envie:
- ✅ Os 3 screenshots acima
- ✅ Qual navegador está usando (Chrome, Firefox, Edge, Safari?)
- ✅ Se viu algum alert (mensagem pop-up) aparecer
- ✅ Exatamente quais campos foram preenchidos

## 🔍 O Que Estamos Investigando

Com essas informações, vou saber:

1. **Se a função está sendo executada**
   - Se não ver logs → função não está sendo chamada
   - Se ver logs → função está sendo chamada

2. **Se os campos existem no DOM**
   - Se ver `❌ Campo X não encontrado` → campo não existe
   - Se não ver erro → campo existe

3. **Se os dados estão chegando**
   - Logs mostram exatamente quais dados a API retornou
   - Vamos ver se telefone, CEP, etc. estão na resposta

4. **Se há erro de JavaScript**
   - Console mostra qualquer erro vermelho
   - Isso pode quebrar a execução

## 🎯 Por Que Isso é Importante?

Já implementamos TODAS as correções possíveis:
- ✅ Removemos dialog de confirmação
- ✅ Adicionamos auto-preenchimento imediato
- ✅ Adicionamos logs detalhados
- ✅ Verificamos que campos existem no HTML

Mas você ainda reporta que só razão social preenche.

**Isso significa uma de três coisas:**

1. **Cache do navegador** - Código antigo ainda carregado
2. **Deploy não ocorreu** - Código novo não está em produção
3. **Problema específico do ambiente** - Algo que só acontece no seu caso

Com os screenshots, vamos descobrir qual é!

## 💡 Dica Rápida

**Para garantir que está com código mais novo:**
1. Feche completamente o navegador
2. Abra novamente
3. Vá direto para https://app.qualicontax.com.br/clientes/novo
4. Pressione Ctrl+F5
5. Abra Console (F12)
6. Teste CNPJ

## 📞 Aguardando Resposta

Assim que me enviar os screenshots e informações, vou:
1. Identificar o problema exato
2. Implementar a correção definitiva
3. Confirmar que está funcionando

**Obrigado pela paciência! Vamos resolver isso! 🚀**
