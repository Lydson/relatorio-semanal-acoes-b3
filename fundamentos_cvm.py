"""
Made by Lydson
Passo 1 - Fundamentos via dados abertos da CVM (DFP + ITR)
Empresas: BBAS3, ITSA4, CXSE3, CMIG4, EGIE3, INTB3 (Intelbras)

O que faz:
  1. Baixa (uma vez, fica em cache) o DFP do ultimo exercicio e o ITR do ano atual
  2. Acha as empresas pelo nome e confere os CNPJs
  3. Calcula: lucro liquido 12m, PL dos controladores e quantidade de acoes
  4. Salva data/fundamentos.csv e imprime um resumo para voce validar

Rodar:  pip install pandas requests   e depois   python passo1_fundamentos_cvm.py
"""
import re
import time
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd
import requests

__author__ = "Lydson"  # Made by Lydson

# ---------------------------------------------------------------- configuracao
ANO_DFP = 2025  # ultimo exercicio fechado (DFP)
ANO_ITR = 2026  # ano do ITR mais recente

BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"
PASTA = Path(__file__).resolve().parent  # tudo fica ao lado do script
CACHE = PASTA / "cvm_cache"
OUT = PASTA / "data"
CACHE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

# ticker -> regex do nome da empresa na CVM (sem acento, maiusculo)
EMPRESAS = {
    "BBAS3": r"^(BANCO DO BRASIL|BCO BRASIL)",
    "ITSA4": r"^ITAUSA",
    "CXSE3": r"^CAIXA SEGURIDADE",
    "CMIG4": r"^(CIA|COMPANHIA)\.? ENERGETICA DE MINAS GERAIS",
    "EGIE3": r"^ENGIE BRASIL ENERGIA",
    "INTB3": r"^INTELBRAS",
}

# A CVM nao informa a escala da quantidade de acoes: algumas empresas enviam em MILHARES.
# Conferimos pelo VPA (PL / acoes) impresso no resumo. Se um VPA parecer 1000x errado, ajuste aqui.
ESCALA_ACOES = {"ITSA4": 1000, "CMIG4": 1000, "CXSE3": 1000}


# ---------------------------------------------------------------- utilitarios
def norm(s) -> str:
    """Tira acento e coloca em maiusculo, para comparar textos."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return s.upper().strip()


def achar_ticker(nome_norm: str):
    for ticker, padrao in EMPRESAS.items():
        if re.match(padrao, nome_norm):
            return ticker
    return None


def baixar(tipo: str, ano: int, max_idade_dias: float | None = None) -> Path:
    """Baixa o ZIP. Se max_idade_dias for informado, baixa de novo quando o cache estiver mais velho."""
    nome = f"{tipo.lower()}_cia_aberta_{ano}.zip"
    destino = CACHE / nome
    if destino.exists():
        idade = (time.time() - destino.stat().st_mtime) / 86400
        if max_idade_dias is None or idade <= max_idade_dias:
            return destino
    url = f"{BASE}/{tipo}/DADOS/{nome}"
    print(f"Baixando {url} ...")
    parcial = destino.with_suffix(".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(parcial, "wb") as f:
            for pedaco in r.iter_content(1024 * 1024):
                f.write(pedaco)
    parcial.rename(destino)
    return destino


def ler(zip_path: Path, padrao_arquivo: str, cnpj_map: dict | None = None) -> pd.DataFrame:
    """Le um CSV de dentro do ZIP, ja filtrando so as empresas-alvo."""
    with zipfile.ZipFile(zip_path) as z:
        nomes = [n for n in z.namelist() if re.search(padrao_arquivo, n)]
        if len(nomes) != 1:
            raise SystemExit(
                f"Esperava 1 arquivo para '{padrao_arquivo}' em {zip_path.name}, "
                f"achei {nomes}.\nArquivos no ZIP: {z.namelist()}"
            )
        partes = []
        with z.open(nomes[0]) as f:
            for ch in pd.read_csv(f, sep=";", encoding="latin-1", dtype=str, chunksize=200_000):
                if cnpj_map is None:
                    ch["ticker"] = ch["DENOM_CIA"].map(norm).map(achar_ticker)
                else:
                    ch["ticker"] = ch["CNPJ_CIA"].map(cnpj_map)
                partes.append(ch[ch["ticker"].notna()])
    df = pd.concat(partes, ignore_index=True)

    if "ORDEM_EXERC" in df.columns:
        df["ordem"] = df["ORDEM_EXERC"].map(norm)  # ULTIMO / PENULTIMO
    if "VL_CONTA" in df.columns:  # aplica a escala (MIL -> x1000)
        esc = df["ESCALA_MOEDA"].map(norm).map({"MIL": 1_000, "UNIDADE": 1}).fillna(1)
        df["valor"] = pd.to_numeric(df["VL_CONTA"], errors="coerce") * esc
    return ultima_versao(df)


def ultima_versao(df: pd.DataFrame) -> pd.DataFrame:
    """Se a empresa reapresentou o documento, fica so a versao mais recente."""
    df = df.copy()
    df["VERSAO"] = pd.to_numeric(df["VERSAO"])
    maior = df.groupby(["ticker", "DT_REFER"])["VERSAO"].transform("max")
    return df[df["VERSAO"] == maior]


# ---------------------------------------------------------------- escolha das contas
_DIAG_FEITO = set()


def lucro_ctrl(df: pd.DataFrame) -> pd.DataFrame:
    """Linha do lucro liquido atribuido aos controladores (grupo 3.xx da DRE)."""
    desc = df["DS_CONTA"].map(norm)
    mask = (
        df["CD_CONTA"].str.startswith("3.")
        & desc.str.contains("CONTROLADOR")
        & ~desc.str.contains(r"\bNAO\b|MINORIT")
    )
    sel = df[mask]
    if sel.empty:  # fallback: lucro consolidado do periodo
        sel = df[df["CD_CONTA"] == "3.11"]
        print("  (sem linha de controladores; usei 3.11 'consolidado')")
        t = df["ticker"].iloc[0] if not df.empty else None
        if t and t not in _DIAG_FEITO:  # diagnostico: mostra as linhas de lucro da DRE (so 1x)
            _DIAG_FEITO.add(t)
            cand = df[desc.str.contains("LUCRO|PREJUIZO|CONTROLADOR")][["CD_CONTA", "DS_CONTA", "valor"]]
            print("  DIAGNOSTICO - linhas de lucro desta DRE:")
            print(cand.to_string(index=False))
    return sel


def um_valor(df: pd.DataFrame, rotulo: str, ticker: str) -> float:
    if len(df) != 1:
        achadas = df[["CD_CONTA", "DS_CONTA"]].values.tolist()
        print(f"  [!] {ticker} | {rotulo}: {len(df)} linhas (esperava 1) -> {achadas}")
        if df.empty:
            return float("nan")
    return float(df.iloc[0]["valor"])


def lucro_valor(df: pd.DataFrame, rotulo: str, ticker: str) -> float:
    """Lucro dos controladores. Se a linha vier zerada (acontece, ex.: CXSE3 no 2T26),
    usa lucro consolidado (3.11) menos a parcela dos nao controladores."""
    v = um_valor(lucro_ctrl(df), rotulo, ticker)
    if pd.isna(v) or v == 0:
        desc = df["DS_CONTA"].map(norm)
        cons = df[df["CD_CONTA"] == "3.11"]
        nc = df[df["CD_CONTA"].str.startswith("3.11.") & desc.str.contains(r"\bNAO\b.*CONTROLADOR|MINORIT")]
        if not cons.empty and float(cons.iloc[0]["valor"]) != 0:
            v_nc = float(nc.iloc[0]["valor"]) if not nc.empty else 0.0
            v = float(cons.iloc[0]["valor"]) - v_nc
            print(f"  ({rotulo}: linha dos controladores zerada; usei 3.11 menos nao controladores)")
    return v


def pl_ctrl(bpp: pd.DataFrame, ticker: str) -> float:
    """PL consolidado menos participacao dos nao controladores."""
    desc = bpp["DS_CONTA"].map(norm)
    cand = bpp[desc.str.match(r"^PATRIMONIO LIQUIDO( CONSOLIDADO)?$") & bpp["CD_CONTA"].str.startswith("2.")]
    if cand.empty:
        print(f"  [!] {ticker}: nao achei a linha de Patrimonio Liquido")
        return float("nan")
    topo = cand.assign(nivel=cand["CD_CONTA"].str.count(r"\.")).sort_values("nivel").iloc[0]
    cod = topo["CD_CONTA"]
    nc = bpp[bpp["CD_CONTA"].str.startswith(cod + ".") & desc.str.contains("NAO CONTROLADOR")]
    nc_val = float(nc.sort_values("CD_CONTA").iloc[0]["valor"]) if not nc.empty else 0.0
    print(f"  PL: conta {cod} '{topo['DS_CONTA']}' = {topo['valor']:,.0f} | nao controladores = {nc_val:,.0f}")
    return float(topo["valor"]) - nc_val


def qt(c: pd.DataFrame, tipo: str, tesouro: bool) -> float:
    """tipo = 'ORDIN' ou 'PREF'. Aceita QT_ACAO_ORDIN_... e QT_ACAO_ORDINARIA_..."""
    alvo = "TESOURO" if tesouro else "CAP_INTEGR"
    cols = [x for x in c.columns if x.startswith(f"QT_ACAO_{tipo}") and x.endswith(alvo)]
    if c.empty or not cols:
        print(f"  [!] sem coluna de acoes {tipo}/{alvo}. Colunas: {list(c.columns)}")
        return 0.0
    v = pd.to_numeric(c.iloc[0][cols[0]], errors="coerce")
    return 0.0 if pd.isna(v) else float(v)


# ---------------------------------------------------------------- principal
def main():
    print("Made by Lydson | Fundamentos B3 via CVM\n")
    zip_dfp = baixar("DFP", ANO_DFP)
    zip_itr = baixar("ITR", ANO_ITR, max_idade_dias=3)  # ITR muda toda semana na CVM

    # 1) DRE anual: aqui descobrimos os CNPJs pelo nome
    dre_dfp = ler(zip_dfp, r"DRE_con_\d{4}\.csv$")
    faltando = [t for t in EMPRESAS if t not in set(dre_dfp["ticker"])]
    if faltando:
        raise SystemExit(f"Nao achei no DFP {ANO_DFP}: {faltando}. Ajuste os regex em EMPRESAS.")

    print("Empresas encontradas (CONFIRA os CNPJs/nomes):")
    cnpj_map = {}
    for t, g in dre_dfp.groupby("ticker"):
        cnpjs = g["CNPJ_CIA"].unique()
        nomes = g["DENOM_CIA"].unique()
        print(f"  {t}: {nomes.tolist()} | {cnpjs.tolist()}")
        if len(cnpjs) != 1:
            raise SystemExit(f"{t} casou com mais de um CNPJ - deixe o regex mais especifico.")
        cnpj_map[cnpjs[0]] = t

    # 2) ITR: DRE, BPP e composicao do capital
    dre_itr = ler(zip_itr, r"DRE_con_\d{4}\.csv$", cnpj_map)
    bpp_itr = ler(zip_itr, r"BPP_con_\d{4}\.csv$", cnpj_map)
    comp_itr = ler(zip_itr, r"composicao_capital_\d{4}\.csv$", cnpj_map)

    linhas = []
    for t in EMPRESAS:
        print(f"\n== {t} ==")
        cnpj = next(c for c, tk in cnpj_map.items() if tk == t)

        # lucro do exercicio fechado
        d = dre_dfp[(dre_dfp["ticker"] == t) & (dre_dfp["ordem"] == "ULTIMO")
                    & (dre_dfp["DT_REFER"] == f"{ANO_DFP}-12-31")]
        ll_ano = lucro_valor(d, "LL exercicio", t)

        # ITR mais recente da empresa
        dt = dre_itr.loc[dre_itr["ticker"] == t, "DT_REFER"].max()
        print(f"  ITR mais recente: {dt}")
        d = dre_itr[(dre_itr["ticker"] == t) & (dre_itr["DT_REFER"] == dt)
                    & dre_itr["DT_INI_EXERC"].str.endswith("-01-01")]  # so acumulado do ano
        ll_atual = lucro_valor(d[d["ordem"] == "ULTIMO"], "LL acumulado atual", t)
        ll_ant = lucro_valor(d[d["ordem"] == "PENULTIMO"], "LL acumulado ano anterior", t)
        ll_12m = ll_ano + ll_atual - ll_ant

        # patrimonio liquido dos controladores
        b = bpp_itr[(bpp_itr["ticker"] == t) & (bpp_itr["DT_REFER"] == dt) & (bpp_itr["ordem"] == "ULTIMO")]
        pl = pl_ctrl(b, t)

        # acoes (composicao do capital)
        c = comp_itr[comp_itr["ticker"] == t]
        c = c[c["DT_REFER"] == dt] if (c["DT_REFER"] == dt).any() else c[c["DT_REFER"] == c["DT_REFER"].max()]
        c = c.sort_values("VERSAO", ascending=False)
        on = qt(c, "ORDIN", False)
        pn = qt(c, "PREF", False)
        t_on = qt(c, "ORDIN", True)
        t_pn = qt(c, "PREF", True)
        esc = ESCALA_ACOES.get(t, 1)
        on, pn, t_on, t_pn = (x * esc for x in (on, pn, t_on, t_pn))

        linhas.append({
            "ticker": t, "cnpj": cnpj, "dt_itr": dt,
            "ll_exercicio": ll_ano, "ll_acum_atual": ll_atual, "ll_acum_ant": ll_ant,
            "ll_12m": ll_12m, "pl_controladores": pl,
            "acoes_on": on - t_on, "acoes_pn": pn - t_pn,
        })

    res = pd.DataFrame(linhas)
    res["roe_pct"] = res["ll_12m"] / res["pl_controladores"] * 100
    res["vpa"] = res["pl_controladores"] / (res["acoes_on"] + res["acoes_pn"])
    res.to_csv(OUT / "fundamentos.csv", index=False)
    # versao para importar no Google Sheets em portugues (separador ; e decimal com virgula)
    res.to_csv(OUT / "fundamentos_sheets.csv", index=False, sep=";", decimal=",")

    resumo = res[["ticker", "dt_itr"]].copy()
    resumo["LL_12m_R$bi"] = (res["ll_12m"] / 1e9).round(2)
    resumo["PL_R$bi"] = (res["pl_controladores"] / 1e9).round(2)
    resumo["ROE_%"] = res["roe_pct"].round(1)
    resumo["acoes_ON_mi"] = (res["acoes_on"] / 1e6).round(1)
    resumo["acoes_PN_mi"] = (res["acoes_pn"] / 1e6).round(1)
    resumo["VPA_R$"] = res["vpa"].round(2)
    print("\n=== RESUMO (valide contra o release de resultados de cada empresa) ===")
    print(resumo.to_string(index=False))
    print(f"\nSalvo em {OUT / 'fundamentos.csv'}")


if __name__ == "__main__":
    main()