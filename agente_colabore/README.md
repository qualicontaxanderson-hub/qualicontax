# Q-Colabore — agente da máquina do funcionário

Programa que roda na máquina do funcionário, vigia as pastas que ele escolher e
manda cada arquivo novo para a nuvem do Qualicontax (a caixa `_ENTRADA`),
autenticado pela **chave dele**. É o irmão do Q-Robô (que faz o mesmo por posto):
a inteligência fica no servidor; o agente é simples.

- **Casa dos dados:** `C:\qcolabore` (config e log). O programa a cria sozinho.
- **Nunca apaga** arquivo do usuário — só **move** para subpastas.
- A **chave** mora só em `C:\qcolabore\config.json`. Nunca em variável de
  ambiente, registro do Windows, ou log.

---

## Como funciona (resumo)

1. Lê `config.json`: a chave e as pastas a vigiar.
2. A cada ~60 s procura arquivo novo nessas pastas.
3. Pergunta ao servidor a **data de corte** (`GET /api/colabore/config`) e
   **ignora** arquivo anterior a ela (essa data é sempre do servidor).
4. Envia cada arquivo (`POST /api/colabore/enviar`, chave no `Bearer`).
5. Pelo resultado:
   | Resposta | O que o agente faz |
   |----------|--------------------|
   | **200 / 409** | move para **`Enviados`** |
   | **413 / 415** | move para **`Nao enviados`** + grava um `.motivo.txt` |
   | **401 / 403** (chave inválida/revogada) | **para de enviar** e avisa na janela (o programa continua aberto para colar a chave nova) |
   | **5xx / sem rede** | deixa onde está e tenta de novo depois |

### Fica de pé sozinho
- **Nunca morre por erro:** exceção num arquivo é anotada no log e o agente segue
  para o próximo; qualquer erro inesperado no ciclo é capturado e o laço continua.
- **Perda de rede / servidor fora / Dropbox indisponível:** não encerra — espera
  e tenta de novo com **intervalo crescente** (dobra até no máximo 5 min), voltando
  ao normal quando reconecta.
- **Heartbeat:** mesmo sem arquivo novo, a consulta periódica ao servidor atualiza
  o "último contato" — o escritório distingue máquina desligada de máquina parada.

### Início com o Windows
Ao **Salvar** a configuração, se a caixa *"Iniciar junto com o Windows"* estiver
marcada (é o padrão), o agente se registra para subir no logon — no perfil do
**próprio usuário**, **sem exigir administrador** (chave `HKCU\...\Run`,
valor `QColabore`, guardando só o caminho do `.exe`, nunca a chave). Desmarcar
remove o registro. A janela de status mostra se está ativo.

---

## Instalar (a partir do .zip)

1. Baixe **`qcolabore-0.2.0.zip`** da pasta do Dropbox
   `/Aplicativos/QUALICONTAX/Q-Colabore/`.
2. **Extraia tudo** para uma pasta fixa — o mais simples é **`C:\qcolabore`**
   (clique direito no `.zip` → *Extrair tudo…*).
3. Rode **`qcolabore.exe`** de dentro dessa pasta.
4. Na janela de configuração: **cole a chave** (gerada em *Configurações ›
   Usuários › Q-Colabore*), **Adicione** a(s) pasta(s) a vigiar, deixe *"Iniciar
   junto com o Windows"* marcada e clique **Salvar**.

Pronto — o agente vai para a **bandeja** (ao lado do relógio) e trabalha sozinho.
Clique no ícone para abrir; botão direito tem **Abrir**, **Configurar…** e
**Sair**. Fechar no **X** minimiza para a bandeja (não encerra).

O log fica em `C:\qcolabore\qcolabore.log` (com rotação; **nunca** grava a chave).

---

## Compilar (gerar o .zip para distribuir)

Tecnologia: **Nuitka em modo PASTA** (`--standalone`, **sem** `--onefile`).
O `--onefile` (e o PyInstaller) descompactam numa pasta temporária a cada
execução — o padrão que o **Defender** marca. Em modo pasta o `.exe` roda direto,
com as peças ao lado.

```powershell
pip install nuitka requests pystray pillow
powershell -ExecutionPolicy Bypass -File .\build_qcolabore.ps1
```

O script:
- deriva a `--file-version` do `__version__` do fonte (não hardcoded);
- gera a pasta `build\qcolabore_agente.dist\` (o `.exe` + DLLs + tcl/tk);
- compacta o **conteúdo** dela na raiz de `build\qcolabore-0.2.0.zip` — a **mesma
  convenção do `qualicontax.zip` do Q-Robô** (raiz do zip = o programa, sem
  subpasta extra).

Publique o `.zip` em `/Aplicativos/QUALICONTAX/Q-Colabore/`, no lugar do `.exe`.
Na primeira vez o Nuitka baixa o compilador **ziglang** — responda **sim**.

---

## Exceção no Windows Defender

Se uma máquina mais rígida barrar o programa, adicione uma exceção para a pasta
onde ele foi extraído:

1. **Segurança do Windows** → **Proteção contra vírus e ameaças**.
2. *Configurações de proteção contra vírus e ameaças* → **Gerenciar configurações**.
3. **Exclusões** → **Adicionar ou remover exclusões** → **Adicionar uma exclusão**
   → **Pasta**.
4. Escolha a pasta onde está o `qcolabore.exe` (ex.: **`C:\qcolabore`**).

A solução definitiva é **assinar** o executável com um certificado de código.

---

## Arquivos deste diretório

| Arquivo | O quê |
|---------|-------|
| `qcolabore_agente.py` | o agente (loop + janelas + bandeja + envio) |
| `build_qcolabore.ps1` | build Nuitka (pasta) + zip |
| `requirements.txt` | dependências (`requests`, `pystray`, `Pillow`) |
| `README.md` | este arquivo |

> A variável de ambiente `QCOLABORE_HOME` troca a casa `C:\qcolabore` por outra
> pasta — usada **só** pela prova automatizada. Em produção não defina.
