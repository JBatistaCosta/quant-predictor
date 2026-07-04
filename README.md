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

## 3. Por que isso resolve o problema do OCR

O leitor de imagens (botões "Ler Estatísticas" e "Ler Odds da Casa") usa
`<input type="file">` para abrir a galeria/câmera do celular. Dentro do
sandbox de artifacts do Claude, essa abertura é bloqueada por segurança. Uma
vez publicado no Vercel, o app roda como um site normal no navegador do seu
celular, com acesso total à câmera e à galeria — os botões devem funcionar
imediatamente, sem nenhuma mudança de código.

## 4. Estrutura do projeto

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
