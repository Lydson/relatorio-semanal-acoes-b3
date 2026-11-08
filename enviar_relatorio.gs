/**
 * Made by Lydson
 * Relatório semanal de ações da B3: preço, P/L, P/VP e ROE por e-mail.
 *
 * Como funciona:
 *  - A aba "Painel" calcula tudo com fórmulas (preço via GOOGLEFINANCE + fundamentos da CVM).
 *  - Este script lê a aba, monta um e-mail em HTML e envia para o dono da planilha.
 *  - Um trigger semanal chama enviarRelatorio() sozinho.
 *
 * Colunas esperadas na aba Painel (linha 1 = cabeçalho):
 *  A ticker | B preço | C ações ON | D ações PN | E LL 12m | F patrimônio líquido
 *  G data-base | H LPA | I P/L | J P/VP | K ROE
 *  (P/L e P/VP são calculados por ação, igual aos sites)
 */

const ABA_PAINEL = 'Painel';
const ABA_FUNDAMENTOS = 'Fundamentos';

// Troque SEU_USUARIO e SEU_REPO pelos do seu repositorio PUBLICO no GitHub. Aqui usei o meu, mas se você estiver tentando me replicar tem que fazer esta mudança.
const URL_FUNDAMENTOS =
  'https://raw.githubusercontent.com/Lydson/relatorio-semanal-acoes-b3/main/data/fundamentos.csv';

const COLUNAS_ESPERADAS = [
  'ticker', 'cnpj', 'dt_itr', 'll_exercicio', 'll_acum_atual', 'll_acum_ant',
  'll_12m', 'pl_controladores', 'acoes_on', 'acoes_pn', 'roe_pct', 'vpa'
];

/**
 * Baixa o CSV que o GitHub Actions atualiza toda semana e substitui a aba Fundamentos.
 * Antes de sobrescrever, valida o cabecalho: se vier algo estranho, nao mexe na aba.
 */
function atualizarFundamentos() {
  const resp = UrlFetchApp.fetch(URL_FUNDAMENTOS, { muteHttpExceptions: true });
  if (resp.getResponseCode() !== 200) {
    throw new Error('HTTP ' + resp.getResponseCode() + ' ao baixar ' + URL_FUNDAMENTOS);
  }
  const linhas = Utilities.parseCsv(resp.getContentText());
  if (linhas.length < 2 || linhas[0].join(',') !== COLUNAS_ESPERADAS.join(',')) {
    throw new Error('CSV com formato inesperado; aba Fundamentos nao foi alterada.');
  }
  // as colunas numericas (a partir da 4a) viram numero; ticker, cnpj e data ficam como texto
  const dados = linhas.map(function (l, i) {
    if (i === 0) return l;
    return l.map(function (v, j) { return (j >= 3 && v !== '' && !isNaN(v)) ? Number(v) : v; });
  });
  const aba = SpreadsheetApp.getActive().getSheetByName(ABA_FUNDAMENTOS);
  aba.clearContents();
  aba.getRange(1, 1, dados.length, dados[0].length).setValues(dados);
  SpreadsheetApp.flush();
}

function enviarRelatorio() {
  let aviso = '';
  try {
    atualizarFundamentos(); // pega o CSV mais recente publicado no GitHub
  } catch (e) {
    Logger.log('Falha ao atualizar fundamentos: ' + e);
    aviso = '<p style="color:#b26a00;font-size:12px">Aviso: não consegui atualizar os ' +
      'fundamentos desta semana; os valores são da última atualização.</p>';
  }
  SpreadsheetApp.flush(); // garante que o GOOGLEFINANCE já calculou
  const aba = SpreadsheetApp.getActive().getSheetByName(ABA_PAINEL);
  if (!aba) throw new Error('Aba "' + ABA_PAINEL + '" não encontrada.');

  const linhas = aba.getRange(2, 1, aba.getLastRow() - 1, 11).getValues()
    .filter(function (l) { return l[0] !== ''; });

  const tz = Session.getScriptTimeZone();
  const hoje = Utilities.formatDate(new Date(), tz, 'dd/MM/yyyy');

  const corpo = linhas.map(function (l) {
    return '<tr>' +
      td('<b>' + l[0] + '</b>', 'left') +
      td(moeda(l[1]), 'right') +
      td(num(l[8], 2), 'right') +
      td(num(l[9], 2), 'right') +
      td(pct(l[10]), 'right') +
      td(trimestre(l[6]), 'center') +
      '</tr>';
  }).join('');

  const html =
    '<div style="font-family:Arial,sans-serif;max-width:560px">' +
    '<h2 style="margin-bottom:4px">Relatório semanal de ações</h2>' +
    '<div style="color:#666;margin-bottom:12px">' + hoje + '</div>' +
    '<table style="border-collapse:collapse;width:100%;font-size:14px">' +
    '<tr style="background:#f1f3f4">' +
    th('Ação', 'left') + th('Preço', 'right') + th('P/L', 'right') +
    th('P/VP', 'right') + th('ROE', 'right') + th('Base', 'center') + '</tr>' +
    corpo + '</table>' +
    '<p style="color:#666;font-size:12px;margin-top:14px">' +
    'Preços: Google Finance (atraso de ~20 min). Lucro, PL e ações: dados abertos da CVM ' +
    '(DFP/ITR), lucro dos últimos 12 meses e PL dos controladores. P/L = preço ÷ LPA e P/VP = preço ÷ VPA ' +
    '(por ação, com todas as classes); ROE = lucro 12m ÷ PL final. ' +
    '"Base" é o trimestre do último balanço. Informativo, não é recomendação de investimento.</p>' +
    aviso +
    '<p style="color:#999;font-size:11px">Made by Lydson</p></div>';

  GmailApp.sendEmail(
    Session.getActiveUser().getEmail(),
    'Relatório semanal de ações - ' + hoje,
    'Veja este e-mail em HTML.',
    { htmlBody: html, name: 'Relatório de Ações' }
  );
}

/** Rode UMA vez para agendar o envio toda segunda-feira, às 8h. */
function criarTriggerSemanal() {
  ScriptApp.getProjectTriggers()
    .filter(function (t) { return t.getHandlerFunction() === 'enviarRelatorio'; })
    .forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('enviarRelatorio')
    .timeBased().onWeekDay(ScriptApp.WeekDay.MONDAY).atHour(8).create();
}

// ---------- formatação ----------
function td(txt, alinhamento) {
  return '<td style="padding:6px 8px;border-bottom:1px solid #e0e0e0;text-align:' + alinhamento + '">' + txt + '</td>';
}
function th(txt, alinhamento) {
  return '<th style="padding:6px 8px;text-align:' + alinhamento + '">' + txt + '</th>';
}
function ehNumero(n) {
  return typeof n === 'number' && !isNaN(n);
}
function num(n, casas) {
  return ehNumero(n) ? n.toFixed(casas).replace('.', ',') : 'n/d';
}
function moeda(n) {
  return ehNumero(n) ? 'R$ ' + num(n, 2) : 'n/d';
}
function pct(n) {
  return ehNumero(n) ? num(n * 100, 1) + '%' : 'n/d'; // a planilha guarda ROE como fração
}
function trimestre(d) {
  const data = d instanceof Date ? d : new Date(d);
  if (isNaN(data.getTime())) return 'n/d';
  return (Math.floor(data.getMonth() / 3) + 1) + 'T' + String(data.getFullYear()).slice(2);
}