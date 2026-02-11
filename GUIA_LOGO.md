# 🎨 Guia: Como Adicionar a Logo da Empresa

## 📍 Localização do Arquivo

Para adicionar a logo da sua empresa ao sistema Qualicontax, siga estas instruções:

### 1. Onde Colocar o Arquivo da Logo

**Diretório:** `static/images/`  
**Nome do arquivo:** `logo.png`

**Caminho completo no repositório:**
```
qualicontax/
├── static/
│   ├── images/
│   │   └── logo.png  ← COLOQUE SUA LOGO AQUI
│   ├── css/
│   └── js/
```

---

## 🖼️ Especificações da Logo

### Formato Recomendado
- **Formato:** PNG (com fundo transparente)
- **Alternativa:** JPG, SVG

### Dimensões Recomendadas
- **Largura:** 180-200 pixels
- **Altura:** 40-60 pixels
- **Proporção:** Horizontal (landscape)
- **Resolução:** 72-150 DPI

### Qualidade
- Use imagem de alta qualidade
- Fundo transparente (PNG) é preferível
- Evite imagens muito pesadas (máximo 200KB)

---

## 📱 Onde a Logo Aparece

A logo da empresa aparece em **3 locais principais** no sistema:

### 1. Sidebar (Barra Lateral) ⭐ Principal
- **Localização:** Topo da barra lateral esquerda
- **Visibilidade:** Sempre visível em todas as páginas
- **Tamanho:** ~180x50 pixels
- **Ao lado:** Texto "Qualicontax" (pode ser customizado)

### 2. Página de Login 🔐
- **Localização:** Centro da tela de login
- **Visibilidade:** Primeira coisa que usuários veem
- **Tamanho:** ~200x60 pixels
- **Contexto:** Branding da aplicação

### 3. Sidebar Colapsada 📱
- **Localização:** Versão mobile/colapsada do menu
- **Visibilidade:** Quando sidebar está minimizada
- **Tamanho:** Ícone reduzido

---

## 🚀 Como Adicionar Sua Logo

### Método 1: Direto no Repositório (Recomendado)

1. **Prepare sua logo:**
   - Salve como `logo.png`
   - Verifique dimensões (180-200px de largura)
   - Certifique-se que tem boa qualidade

2. **Adicione ao repositório:**
   ```bash
   # No diretório do projeto
   cp /caminho/da/sua/logo.png static/images/logo.png
   ```

3. **Commit e push:**
   ```bash
   git add static/images/logo.png
   git commit -m "Add company logo"
   git push
   ```

4. **Resultado:**
   - Logo aparecerá automaticamente após deploy
   - Visível em sidebar e login

### Método 2: Via GitHub (Interface Web)

1. Acesse o repositório no GitHub
2. Navegue até: `static/images/`
3. Clique em "Add file" → "Upload files"
4. Arraste sua `logo.png` para a área de upload
5. Commit com mensagem: "Add company logo"
6. A logo aparecerá após o deploy

### Método 3: Via Upload Direto no Servidor

Se tiver acesso SSH ao servidor:
```bash
# Conecte ao servidor
ssh user@seu-servidor.com

# Navegue até o diretório
cd /caminho/do/app/static/images/

# Faça upload da logo
# (use scp, rsync, ou outro método)
```

---

## 🎨 Customizando o Texto ao Lado da Logo

Se quiser alterar o texto "Qualicontax" que aparece ao lado da logo:

**Arquivo:** `templates/base.html`  
**Linha:** ~26

```html
<div class="logo">
    <img src="{{ url_for('static', filename='images/logo.png') }}" alt="Qualicontax" class="logo-img">
    <span class="logo-text">Qualicontax</span>  ← ALTERE AQUI
</div>
```

**Também em:** `templates/includes/sidebar.html` (linha ~3)

---

## 🔧 Resolução de Problemas

### Logo não aparece após upload

**Possíveis causas:**

1. **Nome do arquivo incorreto**
   - ✅ Deve ser exatamente: `logo.png`
   - ❌ Não: `Logo.png`, `logo.PNG`, `logotipo.png`

2. **Diretório incorreto**
   - ✅ Deve estar em: `static/images/logo.png`
   - ❌ Não: `static/logo.png` ou `images/logo.png`

3. **Cache do navegador**
   - Solução: Limpe o cache (Ctrl+Shift+R)
   - Ou teste em modo anônimo

4. **Permissões do arquivo**
   ```bash
   # Verifique permissões
   ls -la static/images/logo.png
   
   # Corrija se necessário
   chmod 644 static/images/logo.png
   ```

5. **Deploy não foi feito**
   - Certifique-se que fez git push
   - Aguarde o deploy automático no Railway
   - Verifique logs de deploy

### Logo aparece cortada ou deformada

**Soluções:**

1. **Ajuste as dimensões da imagem**
   - Redimensione para 180x50 pixels
   - Mantenha proporção original

2. **Use ferramenta online:**
   - https://www.iloveimg.com/resize-image
   - https://www.img2go.com/resize-image

3. **CSS personalizado (avançado):**
   ```css
   /* Em static/css/style.css */
   .logo-img {
       max-width: 180px;
       max-height: 50px;
       object-fit: contain;
   }
   ```

### Logo de baixa qualidade

**Soluções:**

1. Use versão de maior resolução
2. Exporte em resolução 2x (360x100px)
3. Use formato SVG para qualidade perfeita
4. Evite JPG de baixa qualidade

---

## 📝 Checklist de Verificação

Antes de fazer deploy, verifique:

- [ ] Logo está no formato PNG ou SVG
- [ ] Nome do arquivo é exatamente `logo.png`
- [ ] Arquivo está em `static/images/logo.png`
- [ ] Dimensões estão entre 180-200px de largura
- [ ] Imagem tem boa qualidade
- [ ] Fundo é transparente (se PNG)
- [ ] Arquivo não é muito grande (< 200KB)
- [ ] Commit foi feito com `git add` e `git commit`
- [ ] Push foi feito para o repositório
- [ ] Deploy foi concluído (verifique Railway)

---

## 🎯 Exemplo Visual

```
Sidebar com Logo:
┌─────────────────────────┐
│  [🖼️ Logo] Qualicontax  │
├─────────────────────────┤
│  🔍 Buscar...           │
├─────────────────────────┤
│  📊 Dashboard           │
│  👥 CRM                 │
│  👔 Cliente             │
│  📄 Contrato            │
│  ...                    │
└─────────────────────────┘
```

```
Login com Logo:
┌─────────────────────────┐
│                         │
│      [🖼️ Logo Grande]   │
│                         │
│   ┌───────────────┐     │
│   │ Email         │     │
│   └───────────────┘     │
│   ┌───────────────┐     │
│   │ Senha         │     │
│   └───────────────┘     │
│   [ Entrar ]            │
│                         │
└─────────────────────────┘
```

---

## 💡 Dicas Adicionais

### Para Melhor Resultado:

1. **Use logo horizontal:** Funciona melhor em sidebar
2. **Fundo transparente:** Adapta-se ao tema
3. **Alto contraste:** Legível em qualquer fundo
4. **Simplicidade:** Logos simples funcionam melhor

### Formatos Alternativos:

Se quiser usar outros formatos, altere as referências:

**Para SVG:**
```html
<img src="{{ url_for('static', filename='images/logo.svg') }}" ...>
```

**Para JPG:**
```html
<img src="{{ url_for('static', filename='images/logo.jpg') }}" ...>
```

### Múltiplas Versões:

Pode ter versões diferentes:
- `logo.png` - Versão principal
- `logo-white.png` - Versão branca (para fundos escuros)
- `logo-icon.png` - Apenas ícone (para favicon)
- `logo-full.png` - Logo completa com slogan

---

## 📞 Suporte

Se precisar de ajuda:

1. **Verifique os logs:** Railway logs para erros de deploy
2. **Teste localmente:** `python app.py` e acesse localhost
3. **Consulte documentação:** Outros arquivos `.md` no repositório
4. **Contate suporte:** suporte@qualicontax.com.br

---

## 🎊 Pronto!

Após seguir este guia, sua logo estará:
- ✅ Visível em todas as páginas
- ✅ Profissional e bem posicionada
- ✅ Responsiva em todos os dispositivos
- ✅ Fácil de atualizar no futuro

**Sua marca agora está presente no sistema! 🚀**

---

**Última atualização:** 11 de fevereiro de 2026  
**Versão:** 1.0  
**Status:** ✅ Completo e testado
