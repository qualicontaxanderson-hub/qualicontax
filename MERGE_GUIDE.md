# 🔄 Guia de Merge - Qualicontax

## Situação Atual ✅

Todas as alterações estão no branch: `copilot/create-accounting-management-app`
- ✅ 48 arquivos criados
- ✅ 6.406+ linhas de código
- ✅ Tudo commitado e enviado ao GitHub

## Como Fazer o Merge

### 📱 Opção 1: Via GitHub (Mais Fácil e Recomendado)

1. **Acesse o repositório:**
   ```
   https://github.com/qualicontaxanderson-hub/qualicontax
   ```

2. **Localize o Pull Request:**
   - Você verá um banner amarelo no topo dizendo:
     "copilot/create-accounting-management-app had recent pushes"
   - OU vá na aba "Pull requests"

3. **Abra o Pull Request:**
   - Se não existir, clique em "Compare & pull request"
   - Se já existir, clique no PR existente

4. **Revise as Mudanças:**
   - Veja os arquivos modificados
   - Leia a descrição do PR

5. **Faça o Merge:**
   - Clique no botão verde "Merge pull request"
   - Clique em "Confirm merge"
   - ✅ Pronto! As alterações agora estão na branch main

6. **Opcional - Limpar:**
   - Clique em "Delete branch" para remover o branch de feature

---

### 💻 Opção 2: Via Linha de Comando

```bash
# 1. Ir para a branch main
git checkout main

# 2. Atualizar a branch main
git pull origin main

# 3. Fazer o merge do branch de feature
git merge copilot/create-accounting-management-app

# 4. Enviar as alterações para o GitHub
git push origin main

# 5. (Opcional) Deletar o branch local
git branch -d copilot/create-accounting-management-app

# 6. (Opcional) Deletar o branch remoto
git push origin --delete copilot/create-accounting-management-app
```

---

## Depois do Merge

### ✅ O que acontece:

1. **No GitHub:**
   - As alterações aparecem na branch `main`
   - O código fica visível em: https://github.com/qualicontaxanderson-hub/qualicontax
   - O PR é marcado como "Merged" (roxo)

2. **Para Outros Desenvolvedores:**
   ```bash
   git checkout main
   git pull origin main
   # Agora eles têm todas as suas alterações
   ```

3. **Para Deploy:**
   - Se conectado ao Heroku: Deploy automático
   - Se conectado ao Railway: Deploy automático
   - Manualmente: `git pull` no servidor e reiniciar

---

## 🚀 Depois de Fazer o Merge, Como Usar a Aplicação

### 1. Clone o Repositório (se for em outro computador)
```bash
git clone https://github.com/qualicontaxanderson-hub/qualicontax.git
cd qualicontax
```

### 2. Configure o Ambiente
```bash
# Criar ambiente virtual
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Instalar dependências
pip install -r requirements.txt
```

### 3. Configure as Variáveis de Ambiente
```bash
# Copiar o exemplo
cp .env.example .env

# Editar com suas credenciais do Railway
nano .env  # ou use seu editor favorito
```

### 4. Inicializar o Banco de Dados
```bash
python init_db.py
```
Isso criará:
- 14 tabelas no MySQL
- Usuário admin padrão

### 5. Executar a Aplicação
```bash
python app.py
```

### 6. Acessar no Navegador
```
http://localhost:5000
```

**Login padrão:**
- Email: `admin@qualicontax.com`
- Senha: `admin123`

⚠️ **IMPORTANTE:** Altere a senha do admin após o primeiro login!

---

## 🆘 Problemas Comuns

### "Conflito de Merge"
Se aparecer conflito ao fazer merge:
```bash
# Ver quais arquivos têm conflito
git status

# Editar os arquivos conflitantes
# Procure por <<<<<<, ====== e >>>>>>
# Escolha qual código manter

# Depois de resolver
git add .
git commit -m "Resolve merge conflicts"
git push origin main
```

### "Não Consigo Fazer Merge"
- Certifique-se de ter permissões no repositório
- Se for via GitHub, use a opção 1 (interface web)
- Se for via terminal, certifique-se de estar na branch main

### "As Mudanças Não Aparecem"
- Após o merge, faça `git pull origin main` em sua máquina local
- Limpe o cache do navegador (Ctrl+Shift+R)
- Verifique se está olhando a branch correta no GitHub

---

## 📞 Precisa de Ajuda?

- **Email:** suporte@qualicontax.com
- **GitHub Issues:** Crie um issue no repositório
- **Documentação:** Veja README.md, QUICKSTART.md, IMPLEMENTATION.md

---

## ✅ Checklist Pós-Merge

Após fazer o merge, verifique:

- [ ] As alterações aparecem na branch main do GitHub
- [ ] Consegue clonar e executar o projeto
- [ ] O banco de dados inicializa corretamente
- [ ] Consegue fazer login na aplicação
- [ ] O dashboard carrega sem erros
- [ ] Os clientes podem ser cadastrados
- [ ] Trocou a senha do admin padrão

---

**Última Atualização:** 2026-02-09
**Versão:** 1.0.0
