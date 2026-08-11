// server/index.js
// Ponto de entrada usado pelo Elastic Beanstalk ("npm start"). Sobe o mesmo
// app Express de server/app.js como um servidor HTTP comum.
import app from './app.js';

const port = process.env.PORT || 8080;
app.listen(port, () => {
  console.log(`quant-predictor rodando na porta ${port}`);
});
