"""
Indicateur i053 : Distance moyenne entre le domicile et le tavail

Source : INSEE
URL : https://ecologie.data.gouv.fr/indicators/67cad6e4f4bd7e3f3d82ff9d
Dernières données disponibles : 2020
Règle d'agglomération bassin de vie → EPCI avec une moyenne

maj script : 03/02/2026
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from typing import Iterable, Iterator

from pathlib import Path
import pandas as pd

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Indicator, IndicatorValue


logger = logging.getLogger(__name__)


@dataclass
class RawValue:
    epci_id: str
    indicator_id: str
    year: int
    value: float
    unit: str | None = None
    source: str | None = None
    meta: dict | None = None


def fetch_raw_csv(filename: str, sep=";", header=2) -> pd.DataFrame:
    script_dir = Path(__file__).parent  # scripts/api/
    csv_path = script_dir.parent / "source" / filename  # scripts/source/
    return pd.read_csv(csv_path, sep=sep, header=header)


def fetch_bdv_epci_mapping(filename: str = "epci_membres.csv") -> pd.DataFrame:
    script_dir = Path(__file__).parent
    mapping_path = script_dir.parent / "source" / filename

    df = pd.read_csv(mapping_path, sep=",", dtype={"siren": str, "bassin_vie": str})

    df = df[["siren", "bassin_vie"]]

    # Exclusion TOM / îles
    df = df[(df["siren"] != "000000000") & (df["bassin_vie"] != "00000")]

    return df.drop_duplicates().rename(columns={"siren": "epci_id"})


def clean_i053_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(
        columns={
            "Code": "bassin_vie",
            "Libellé": "bdv_lib",
            "Distance moyenne entre le domicile et le travail selon la CSP": "value",
        }
    )

    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    return df[["bassin_vie", "value"]]


def aggregate_bdv_to_epci(
    df_indicator: pd.DataFrame, df_mapping: pd.DataFrame
) -> pd.DataFrame:

    df = df_mapping.merge(df_indicator, on="bassin_vie", how="left")

    # moyenne simple
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.groupby("epci_id", as_index=False).mean(numeric_only=True)

    return df.drop_duplicates()


def finalize_i053_df(df: pd.DataFrame) -> pd.DataFrame:
    df["indicator_id"] = "i053"
    df["year"] = 2020
    df["unit"] = "km"
    df["source"] = "INSEE"

    return df[
        ["epci_id", "indicator_id", "year", "value", "unit", "source"]
    ].drop_duplicates()


def complete_with_all_epci(df: pd.DataFrame) -> pd.DataFrame:
    epci_df = fetch_bdv_epci_mapping()

    return epci_df.merge(df, on="epci_id", how="left")


def transform_df_to_raw_values(df: pd.DataFrame) -> Iterator[RawValue]:
    for _, row in df.iterrows():
        yield RawValue(
            epci_id=str(row["epci_id"]),
            indicator_id=row["indicator_id"],
            year=int(row["year"]),
            value=None if pd.isna(row["value"]) else float(row["value"]),
            unit=row["unit"],
            source=row["source"],
            meta={},
        )


def persist_values(session, rows: Iterable[RawValue]) -> int:
    """Insérer ou mettre à jour les valeurs brutes."""

    inserted = 0
    for row in rows:
        record = IndicatorValue(
            epci_id=row.epci_id,
            indicator_id=row.indicator_id,
            year=row.year,
            value=row.value,
            unit=row.unit,
            source=row.source,
            meta=row.meta or {},
        )
        session.merge(record)
        inserted += 1
    session.commit()
    return inserted


def ensure_indicator_exists(session, indicator_id: str) -> None:
    """Optionnel : vérifier que l'indicateur ciblé existe côté base."""

    exists = session.execute(
        select(Indicator.id).where(Indicator.id == indicator_id)
    ).scalar_one_or_none()
    if not exists:
        raise ValueError(
            f"L'indicateur {indicator_id} est introuvable en base. Importez d'abord la table de référence."
        )


def run(csv_filename: str) -> None:
    session = SessionLocal()
    try:
        ensure_indicator_exists(session, "i053")  # adapter avec l'indicateur_id
        df_raw = fetch_raw_csv(csv_filename)
        df_i053 = clean_i053_df(df_raw)

        df_mapping = fetch_bdv_epci_mapping()

        df_epci = aggregate_bdv_to_epci(df_i053, df_mapping)

        # complétion exhaustive EPCI
        df_epci = complete_with_all_epci(df_epci)

        df_final = finalize_i053_df(df_epci)

        rows = list(transform_df_to_raw_values(df_final))
        if not rows:
            logger.warning("Aucune ligne à insérer")
            return
        count = persist_values(session, rows)
        logger.info("%s lignes upsertées dans valeur_indicateur", count)
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import CSV -> valeur_indicateur (indicateur i053)"
    )  # "Import CSV -> valeur_indicateur"
    parser.add_argument(
        "--csv",
        default="i053.csv",
        help="Nom du fichier CSV à importer (dans scripts/source/)",
    )  # adapter le default
    parser.add_argument(
        "--save",
        action="store_true",
        help="Output CSV (dans scripts/output/)",
    )  # adapter le default
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'insère rien en base, affiche seulement les lignes qui seraient importées.",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    parser = build_parser()
    args = parser.parse_args()

    if args.dry_run:
        df_raw = fetch_raw_csv(args.csv)
        df_i053 = clean_i053_df(df_raw)
        df_mapping = fetch_bdv_epci_mapping()
        df_epci = aggregate_bdv_to_epci(df_i053, df_mapping)
        df_epci = complete_with_all_epci(df_epci)
        df_final = finalize_i053_df(df_epci)

        rows = list(transform_df_to_raw_values(df_final))
        print(json.dumps([row.__dict__ for row in rows], indent=2, ensure_ascii=False))

        if args.save:
            script_dir = Path(__file__).parent
            csv_path = script_dir.parent / "output" / args.csv
            df_final.to_csv(csv_path, index=False)
            print(f"Fichier sauvegardé : {csv_path}")

        return

    run(args.csv)


if __name__ == "__main__":
    main()
