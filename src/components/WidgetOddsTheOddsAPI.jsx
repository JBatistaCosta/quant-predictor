// src/components/WidgetOddsTheOddsAPI.jsx
// Widget oficial embutido da the-odds-api.com pro Brasileirão Série A —
// accessKey é uma chave de widget (feita pra ir direto no HTML público, não
// uma chave de API server-side). O widget não tem parâmetro pra filtrar um
// jogo específico — sempre mostra a lista completa de eventos do campeonato,
// por isso aparece igual tanto na página da liga quanto na de cada jogo.
export default function WidgetOddsTheOddsAPI() {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-3 h-fit lg:sticky lg:top-4">
      <iframe
        title="Sports Odds Widget"
        style={{ width: '20rem', height: '25rem', border: '1px solid black', maxWidth: '100%' }}
        src="https://widget.the-odds-api.com/v1/sports/soccer_brazil_campeonato/events/?accessKey=wk_291668bca6085fcfe0aafb9d5fcc5f5c&bookmakerKeys=pinnacle&oddsFormat=decimal&markets=h2h%2Cspreads%2Ctotals&marketNames=h2h%3AMoneyline%2Cspreads%3ASpreads%2Ctotals%3AOver%2FUnder"
      />
    </div>
  );
}
