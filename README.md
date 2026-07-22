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

## 4. Por que isso resolve o problema do OCR

O leitor de imagens (botões "Ler Estatísticas" e "Ler Odds da Casa") usa
`<input type="file">` para abrir a galeria/câmera do celular. Dentro do
sandbox de artifacts do Claude, essa abertura é bloqueada por segurança. Uma
vez publicado no Vercel, o app roda como um site normal no navegador do seu
celular, com acesso total à câmera e à galeria — os botões devem funcionar
imediatamente, sem nenhuma mudança de código.

## 5. Estrutura do projeto

```
├── index.html          # HTML raiz
├── src/
│   ├── main.jsx         # ponto de entrada do React
│   ├── App.jsx          # todo o app (calculadora, OCR, Monte Carlo, Kelly)
│   └── index.css        # diretivas do Tailwind
├── tailwind.config.js
├── postcss.config.js
├── vite.config.js
└── package.json
```
