# Quant System Predictor 8.0

App de análise quantitativa de apostas (Poisson, Elo, Monte Carlo, Kelly) com
leitor de imagens por IA (OCR via Claude Vision).

## 1. Rodar localmente (para testar antes de publicar)

Pré-requisito: ter o Node.js instalado (baixe em https://nodejs.org, versão LTS).

No terminal, dentro desta pasta:

```
npm install
npm run dev
```

Isso abre o app em http://localhost:5173 no seu navegador. Aqui, com o app
rodando fora do sandbox do Claude, o seletor de arquivos e a câmera já
funcionam normalmente.

## 2. Publicar no Vercel (deixa o app com uma URL pública)

### Opção A — Mais simples (sem GitHub), usando a CLI do Vercel

1. Crie uma conta grátis em https://vercel.com (pode entrar com Google/GitHub/email).
2. No terminal, instale a ferramenta de linha de comando da Vercel:
   ```
   npm install -g vercel
   ```
3. Ainda dentro desta pasta do projeto, rode:
   ```
   vercel
   ```
4. Ele vai fazer algumas perguntas (Set up and deploy? → yes; link to
   existing project? → no; nome do projeto → pode aceitar o padrão). Na
   primeira vez, ele abre o navegador para você logar na sua conta Vercel.
5. Ao final, ele imprime uma URL (algo como
   `https://quant-system-predictor.vercel.app`). Essa é a versão de teste
   (preview).
6. Para publicar a versão definitiva (produção), rode:
   ```
   vercel --prod
   ```

### Opção B — Via GitHub (melhor se for atualizar o código com frequência)

1. Crie um repositório no GitHub e suba esta pasta:
   ```
   git init
   git add .
   git commit -m "Quant System Predictor 8.0"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/NOME_DO_REPO.git
   git push -u origin main
   ```
2. Entre em https://vercel.com/new, clique em "Import Git Repository" e
   selecione o repositório.
3. A Vercel detecta automaticamente que é um projeto Vite — não precisa mudar
   nada nas configurações. Clique em "Deploy".
4. Depois disso, toda vez que você der `git push`, a Vercel atualiza o site
   sozinha (deploy automático).

## 3. Publicar no Firebase Hosting (alternativa ao passo 2)

**Importante: só o front-end (a parte visual, `dist/`) vai pro Firebase Hosting.**
As 12 funções em `api/*.js` (Supabase, API-Football, OCR, etc.) e os crons
(`vercel.json`) continuam rodando no Vercel — Firebase Hosting só serve
arquivos estáticos, não roda esse código de servidor. Por isso o front-end
compilado pro Firebase precisa saber a URL do Vercel pra chamar a API
(`src/utils/apiUrl.js` cuida disso, lendo `VITE_API_BASE_URL`).

1. Instale a CLI do Firebase e faça login (uma vez só):
   ```
   npm install -g firebase-tools
   firebase login
   ```
2. Garanta que existe um `.env` ou `.env.local` (não commitado) com
   `VITE_SUPABASE_URL` e `VITE_SUPABASE_KEY` — os mesmos usados em `npm run dev`.
   Sem isso o build quebra em runtime (o app não consegue falar com o Supabase).
3. Rode:
   ```
   npm run deploy:firebase
   ```
   Isso builda com `.env.firebase` (define `VITE_API_BASE_URL` pro domínio de
   produção do Vercel, `https://quant-predictor.vercel.app`) e publica o
   `dist/` no projeto Firebase configurado em `.firebaserc` (`agilsgh-65463878-f64e7`).
4. O CORS das funções do Vercel já libera os domínios padrão do Firebase
   Hosting (`*.web.app` / `*.firebaseapp.com`) e `localhost:5173` — ver
   `api/_lib/cors.js`. Se depois você apontar um domínio próprio pro Firebase
   Hosting, adicione-o na variável de ambiente `CORS_EXTRA_ORIGINS` no painel
   do Vercel (aceita lista separada por vírgula) e faça redeploy das funções.

## 4. Publicar na AWS (Elastic Beanstalk) — outra alternativa ao passo 2

**Diferente do Firebase (passo 3), aqui front-end E as 12 funções de `api/*.js`
rodam juntos, no mesmo servidor e no mesmo domínio** (`server/app.js` reúne
tudo num app Express só). Vantagens práticas: não existe limite de "12
funções serverless" (é um servidor único, não funções separadas) e a
navegação fica mais fluida — o navegador nunca precisa sair do domínio da
AWS pra chamar a API, então não depende de CORS liberado noutro domínio.

Você **não precisa saber nada de AWS de antemão** — o passo a passo abaixo
cobre desde criar a conta até publicar. É mais longo que o Vercel porque a
AWS não tem um comando único tipo `vercel`. Tem dois jeitos de publicar (veja
o passo 4.3): **pelo Console no navegador** (sem instalar nada, sem digitar
chave secreta — melhor se você está num computador público/compartilhado) ou
**pela linha de comando** (mais rápido pra atualizar com frequência, mas só
recomendado no seu próprio computador).

### 4.1. Criar a conta AWS (uma vez só)

1. Entre em https://aws.amazon.com/pt/, clique em "Criar uma conta da AWS" e
   siga o cadastro (pede cartão de crédito pra confirmar identidade, mas o
   Elastic Beanstalk em si não cobra nada extra — você paga só a instância de
   servidor que ele cria por trás, e o nível gratuito da AWS cobre isso por
   12 meses num uso pequeno como esse app).

### 4.2. Criar um usuário com permissão (IAM) e pegar as chaves de acesso

A AWS não deixa fazer login "pela linha de comando" com o e-mail/senha da
conta — precisa de um **usuário IAM** com uma **chave de acesso** (o
equivalente a usuário+senha, só que pra ferramentas). É isso que "configurar
permissões e chaves" quer dizer, na prática:

1. Faça login em https://console.aws.amazon.com/ com a conta criada acima.
2. Na busca do topo, digite "IAM" e entre no serviço IAM.
3. Menu lateral → **Users** → **Create user**.
4. Dê um nome (ex.: `quant-predictor-deploy`) → **Next**.
5. Em "Permissions options", escolha **Attach policies directly** e marque
   `AdministratorAccess-AWSElasticBeanstalk` (política pronta da própria AWS,
   já cobre tudo que o Elastic Beanstalk precisa) → **Next** → **Create user**.
6. Clique no usuário recém-criado → aba **Security credentials** → seção
   "Access keys" → **Create access key**.
7. Escolha o caso de uso **Command Line Interface (CLI)** → confirme o aviso
   → **Next** → **Create access key**.
8. A AWS mostra a **Access key ID** e a **Secret access key** — essa é a
   ÚNICA vez que a chave secreta aparece na tela. Clique em **Download .csv
   file** e guarde esse arquivo num lugar seguro (nunca comitar no Git).

Se mais tarde algo falhar com erro de permissão ("not authorized to perform
...") ou de credencial ("Unable to locate credentials" / "InvalidClientTokenId"),
volta nessa tela (IAM → Users → o usuário → Security credentials) pra
conferir se a chave ainda existe e criar uma nova se precisar (chave antiga
pode ser desativada em "Actions" sem afetar nada até você confirmar que a
nova funciona).

**Essa chave (Access key ID + Secret access key) só é usada no caminho "linha
de comando" (4.4-B) — se você vai publicar pelo Console (4.3-A, recomendado
num computador público/compartilhado), pode pular a criação da chave e ir
direto pro próximo passo.** Nunca digite a Secret access key num computador
que não é seu — se você já criou a chave e não vai usá-la agora, mais seguro
é desativá-la ou excluí-la (mesma tela, botão "Actions") até precisar de
verdade num computador de confiança.

### 4.3. Como publicar — escolha um caminho

**4.3-A — Pelo Console da AWS, sem instalar nada (recomendado se você está
num computador público ou compartilhado)**

Não precisa instalar nada nem digitar nenhuma chave secreta — só usa o
navegador, já logado no Console da AWS.

1. Baixe o código: no GitHub, na página do repositório/branch, clique em
   **Code** (botão verde) → **Download ZIP**. Isso baixa a pasta inteira do
   projeto sem precisar de `git` nem terminal.
2. No Console da AWS (https://console.aws.amazon.com/), busque **Elastic
   Beanstalk** → **Create application**.
3. Preencha:
   - **Application name**: `quant-predictor` (ou o nome que quiser).
   - **Platform**: **Node.js** (deixe a versão mais recente sugerida).
   - **Application code**: escolha **Upload your code** → **Choose file** →
     selecione o `.zip` baixado no passo 1.
   - Em "Presets", escolha **Single instance (free tier eligible)** — fica
     dentro do nível gratuito.
4. Antes de clicar em criar, adicione as chaves que os endpoints precisam:
   role até **Configure more options** → no bloco **Software** clique em
   **Edit** → seção **Environment properties** → adicione uma por uma (nome
   e valor) as mesmas chaves já usadas no painel do Vercel: `SUPABASE_URL`,
   `SUPABASE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `API_FOOTBALL_KEY`,
   `FOOTBALL_DATA_KEY`, `ODDS_API_KEY`, `ODDSPAPI_KEY`, `THE_STATSAPI_KEY`,
   `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `CRON_SECRET`,
   `GITHUB_ACTIONS_PAT` (só as que você já usa — pode pular o resto) →
   **Save**.
5. Clique em **Create app**. Leva uns 5-10 minutos na primeira vez — a barra
   de status fica em "Launching environment" e depois vira **Health: Ok**
   (verde) quando terminar.
6. A URL pública do site aparece no topo da página do ambiente (algo como
   `quant-predictor-env.xxxxx.sa-east-1.elasticbeanstalk.com`) — clique pra
   abrir.
7. **Pra atualizar depois** (ex. depois de uma mudança de código): baixe um
   ZIP novo do GitHub (passo 1) e, na página do ambiente, clique em **Upload
   and deploy** → selecione o novo ZIP → **Deploy**.
8. Ao terminar de usar o computador público, saia da sessão do Console
   (canto superior direito → **Sign out**) antes de sair do navegador.

Pule o restante da seção 4 (4.4 a 4.7) se for usar só esse caminho — eles
cobrem o mesmo processo pela linha de comando, útil se você for atualizar o
código com frequência a partir do SEU PRÓPRIO computador.

**4.3-B — Pela linha de comando (CLI)**

Mais rápido pra quem atualiza com frequência, mas precisa instalar
ferramentas e digitar a Secret access key do passo 4.2 — só faça isso no seu
próprio computador, nunca num computador público/compartilhado.

### 4.4. Instalar as ferramentas de linha de comando (uma vez só)

No terminal:

```
pip install awsebcli --upgrade --user
```

(precisa do Python instalado — se não tiver, baixe em https://python.org.
Alternativa sem Python: instalar via `brew install awsebcli` no Mac, ou ver
https://github.com/aws/aws-elastic-beanstalk-cli-setup pro Windows.)

Depois, rode:

```
eb --version
```

Se aparecer a versão, funcionou.

### 4.5. Conectar as chaves da AWS nesta pasta e criar o ambiente

Ainda no terminal, dentro da pasta deste projeto:

```
eb init
```

Ele pergunta a região (escolha uma perto de você, ex. `sa-east-1` — São
Paulo), depois pede a **Access key ID** e a **Secret access key** do passo
4.2 (cole os valores do arquivo .csv baixado). Em seguida:
- "Select an application" → crie uma nova, aceite o nome padrão.
- Se perguntar a plataforma, escolha **Node.js** (versão mais recente
  disponível).
- Se perguntar sobre CodeCommit → **No**. Sobre SSH → pode responder **No**
  também (não é necessário pro deploy funcionar).

Agora crie o ambiente (isso já sobe a primeira versão do site — demora uns
5-10 minutos na primeira vez):

```
eb create quant-predictor-prod
```

### 4.6. Configurar as variáveis de ambiente (chaves do Supabase, APIs, etc.)

As funções em `api/*.js` (Supabase, API-Football, OCR por IA etc.) leem
essas chaves de variáveis de ambiente do servidor — sem elas, os endpoints
respondem erro. Rode (troque `SEU_VALOR_AQUI` pelas chaves reais, as mesmas
já usadas no painel do Vercel):

```
eb setenv SUPABASE_URL=SEU_VALOR_AQUI SUPABASE_KEY=SEU_VALOR_AQUI SUPABASE_SERVICE_ROLE_KEY=SEU_VALOR_AQUI API_FOOTBALL_KEY=SEU_VALOR_AQUI FOOTBALL_DATA_KEY=SEU_VALOR_AQUI ODDS_API_KEY=SEU_VALOR_AQUI ODDSPAPI_KEY=SEU_VALOR_AQUI THE_STATSAPI_KEY=SEU_VALOR_AQUI ANTHROPIC_API_KEY=SEU_VALOR_AQUI GEMINI_API_KEY=SEU_VALOR_AQUI CRON_SECRET=SEU_VALOR_AQUI GITHUB_ACTIONS_PAT=SEU_VALOR_AQUI
```

(Só precisa preencher as que você já usa hoje no Vercel — pode omitir as
que não usa, ex. se não usa OCR com Gemini, pode pular `GEMINI_API_KEY`.)

### 4.7. Publicar atualizações depois da primeira vez

Sempre que quiser subir uma mudança de código pra AWS:

```
eb deploy
```

Pra ver a URL pública do site (algo como
`quant-predictor-prod.xxxxx.sa-east-1.elasticbeanstalk.com`):

```
eb open
```

### 4.8. Importante: os crons (tarefas agendadas) continuam no Vercel

Essa publicação na AWS cobre o site e a API sob demanda. As 3 tarefas
agendadas (`vercel.json` — sincronizar partidas, odds, Elo) continuam rodando
só no Vercel, de propósito: rodar a MESMA tarefa a partir de dois lugares ao
mesmo tempo arrisca gravação duplicada/concorrente no banco Supabase. Se no
futuro você quiser aposentar o Vercel de vez, isso pede migrar os crons pra
AWS EventBridge Scheduler (chamando os mesmos endpoints com o header
`Authorization: Bearer <CRON_SECRET>`) — decisão separada, não feita aqui.

## 5. Por que isso resolve o problema do OCR

O leitor de imagens (botões "Ler Estatísticas" e "Ler Odds da Casa") usa
`<input type="file">` para abrir a galeria/câmera do celular. Dentro do
sandbox de artifacts do Claude, essa abertura é bloqueada por segurança. Uma
vez publicado no Vercel, o app roda como um site normal no navegador do seu
celular, com acesso total à câmera e à galeria — os botões devem funcionar
imediatamente, sem nenhuma mudança de código.

## 6. Estrutura do projeto

```
├── index.html          # HTML raiz
├── src/
│   ├── main.jsx         # ponto de entrada do React
│   ├── App.jsx          # todo o app (calculadora, OCR, Monte Carlo, Kelly)
│   └── index.css        # diretivas do Tailwind
├── api/                 # funções serverless (Vercel) — também usadas pela AWS
├── server/              # app Express que serve front-end + api/ juntos (AWS)
├── tailwind.config.js
├── postcss.config.js
├── vite.config.js
└── package.json
```
