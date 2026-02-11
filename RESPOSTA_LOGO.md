# 🎯 RESPOSTA RÁPIDA: Onde Colocar a Logo da Empresa

## 📍 Localização da Logo

**Coloque sua logo aqui:**
```
static/images/logo.png
```

## 🚀 Passo a Passo Rápido

### 1. Prepare sua Logo
- Formato: PNG (fundo transparente)
- Tamanho: 180-200 pixels de largura
- Nome: `logo.png`

### 2. Adicione ao Repositório

**Opção A - GitHub (mais fácil):**
1. Acesse o repositório no GitHub
2. Entre na pasta `static/images/`
3. Clique em "Add file" → "Upload files"
4. Arraste seu arquivo `logo.png`
5. Commit: "Add company logo"

**Opção B - Git (linha de comando):**
```bash
# Copie sua logo para a pasta correta
cp /caminho/da/sua/logo.png static/images/logo.png

# Adicione ao Git
git add static/images/logo.png

# Commit
git commit -m "Add company logo"

# Push
git push
```

### 3. Aguarde o Deploy
- Railway faz deploy automaticamente
- Aguarde 1-2 minutos
- Limpe o cache do navegador (Ctrl+Shift+R)

## 📱 Onde a Logo Aparece

Sua logo aparecerá em **3 lugares**:
1. ✅ **Sidebar** (barra lateral) - em todas as páginas
2. ✅ **Tela de Login** - primeira coisa que usuários veem
3. ✅ **Mobile** - versão responsiva

## 📖 Documentação Completa

Para instruções detalhadas, consulte:
- **Guia completo:** [GUIA_LOGO.md](GUIA_LOGO.md)
- **Quick reference:** [static/images/README.md](static/images/README.md)

## ✅ Checklist

Antes de fazer upload:
- [ ] Logo está em formato PNG
- [ ] Nome do arquivo é exatamente `logo.png`
- [ ] Dimensões são 180-200px de largura
- [ ] Fundo é transparente (preferencial)
- [ ] Qualidade está boa

Após upload:
- [ ] Commit foi feito
- [ ] Push foi enviado
- [ ] Deploy completou
- [ ] Cache do navegador foi limpo

## 🎨 Exemplo Visual

```
Estrutura do Repositório:
qualicontax/
├── static/
│   ├── images/
│   │   └── logo.png  ← COLOQUE AQUI
│   ├── css/
│   └── js/
```

```
Como Aparece no App:
┌─────────────────────────┐
│  [Logo] Qualicontax     │ ← Sidebar
├─────────────────────────┤
│  Dashboard              │
│  Clientes               │
│  ...                    │
└─────────────────────────┘
```

## 💡 Dica Extra

Se não tiver logo pronta:
- Use Canva.com (gratuito)
- Faça logo simples e profissional
- Exporte em PNG com fundo transparente

## 🆘 Problemas?

**Logo não aparece?**
1. Verifique o nome: deve ser `logo.png` (minúsculas)
2. Verifique a pasta: `static/images/`
3. Limpe o cache: Ctrl+Shift+R
4. Aguarde o deploy completar

**Logo cortada?**
- Redimensione para 180x50 pixels
- Use ferramenta online: iloveimg.com/resize-image

## ✨ Pronto!

É só isso! Sua logo aparecerá automaticamente em todo o sistema.

**Documentação completa:** [GUIA_LOGO.md](GUIA_LOGO.md)

---

**Criado:** 11 de fevereiro de 2026  
**Status:** ✅ Completo e testado
