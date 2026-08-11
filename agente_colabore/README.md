# Q-Colabore — agente da máquina do funcionário

Programa que roda na máquina do funcionário, vigia as pastas que ele escolher e
manda cada arquivo novo para a nuvem do Qualicontax (a caixa `_ENTRADA`),
autenticado pela **chave dele**. É o irmão do Q-Robô (que faz o mesmo por posto):
a inteligência fica no servidor; o agente é simples.

- **Casa:** `C:\qcolabore` (executável, configuração e log ficam aqui).
- **Nunca apaga** arquivo do usuário — só **move** para subpastas.
- A **chave** mora só em `C:\qcolabore\config.json`. Nunca em variável de
  ambiente, registro do Windows, ou log.

---

## Como funciona (resumo)

1. Lê `config.json`: a chave e as pastas a vigiar.
2. A cada ~60 s procura arquivo novo nessas pastas.
3. Pergunta ao servidor a **data de corte** (`GET /api/colabore/config`) e
   **ignora** arquivo anterior a ela (essa data é sempre do servidor, nunca
   decidida na máquina).
4. Envia cada arquivo (`POST /api/colabore/enviar`, chave no `Bearer`).
5. Pelo resultado:
   | Resposta | O que o agente faz |
   |----------|--------------------|
   | **200 / 409** (recebido ou já existia) | move para a subpasta **`Enviados`** |
   | **413 / 415** (grande demais / extensão negada) | move para **`Nao enviados`** + grava um `.motivo.txt` ao lado |
   | **401 / 403** (chave inválida ou revogada) | **para de enviar** e avisa na janela |
   | **5xx / sem rede** | deixa onde está e tenta de novo depois |

As subpastas `Enviados` e `Nao enviados` nascem **dentro de cada pasta vigiada**.

---

## Primeira execução (configuração)

1. Rode `C:\qcolabore\qcolabore.exe`.
2. Na janela que abre, **cole a chave** (gerada no sistema em
   *Configurações › Usuários › Q-Colabore*) e **adicione as pastas** a vigiar
   (pode ser mais de uma).
3. Clique **Salvar**. A partir daí o agente sobe **minimizado** e trabalha
   sozinho.

Para reabrir a configuração depois (trocar a chave, mudar pastas): abra a janela
pela barra de tarefas e clique **Configurar…**.

### O que a janela mostra
- Se está **conectado**;
- Quantos arquivos **enviou hoje** e qual foi o **último**;
- Se há algo em **`Nao enviados`** esperando atenção.

O log fica em `C:\qcolabore\qcolabore.log` (com rotação; **nunca** grava a chave).

---

## Compilar (gerar o executável)

Tecnologia: **Nuitka** (compila Python para C). PyInstaller foi tentado no
Q-Robô e o **Windows Defender travou** o `.exe`; por isso Nuitka.

```powershell
pip install nuitka requests
powershell -ExecutionPolicy Bypass -File .\build_qcolabore.ps1
```

O script:
- deriva a `--file-version` do `__version__` do próprio fonte (não é digitada à
  mão — a próxima versão não depende de alguém lembrar de trocar);
- gera `build\qcolabore.exe` e copia para `C:\qcolabore\qcolabore.exe`
  (sem tocar no `config.json` existente).

Na primeira vez o Nuitka pergunta se pode baixar o compilador MinGW64 — responda
**sim** (ou tenha o MSVC/Build Tools instalado).

---

## Exceção no Windows Defender

Mesmo com Nuitka, uma máquina mais rígida pode barrar um executável novo e sem
assinatura. Se isso acontecer, adicione uma exceção para a pasta do agente:

1. **Segurança do Windows** → **Proteção contra vírus e ameaças**.
2. Em *Configurações de proteção contra vírus e ameaças*, clique **Gerenciar
   configurações**.
3. Desça até **Exclusões** → **Adicionar ou remover exclusões** → **Adicionar
   uma exclusão** → **Pasta**.
4. Escolha **`C:\qcolabore`**.

Isso libera o agente sem baixar a proteção do resto da máquina. (A solução
definitiva é **assinar** o executável com um certificado de código — fica para
quando houver um.)

---

## Arquivos deste diretório

| Arquivo | O quê |
|---------|-------|
| `qcolabore_agente.py` | o agente (loop + janelas + envio) |
| `build_qcolabore.ps1` | build Nuitka (deriva a versão do fonte) |
| `requirements.txt` | dependência (`requests`) |
| `README.md` | este arquivo |

> A variável de ambiente `QCOLABORE_HOME` troca a casa `C:\qcolabore` por outra
> pasta — usada **só** pela prova automatizada, para não mexer na instalação
> real. Em produção não defina.
