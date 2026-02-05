"""
Indicateur i128 : Part des jeunes non insérés (ni en emploi, ni scolarisés - NEET) 2022

Source : observatoire des territoires
URL : https://www.observatoire-des-territoires.gouv.fr/outils/cartographie-interactive/#c=indicator&i=insee_rp_hist_xxxx.part_non_inseres&s=2022&view=map78  
Dernières données disponibles : 2022  

maj script : 05/02/2026
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
    script_dir = Path(__file__).parent      # scripts/api/
    csv_path = script_dir.parent / "source" / filename  # scripts/source/i128.csv
    return pd.read_csv(csv_path, sep=sep, header=header)


def fetch_epci_mapping(filename: str = "epci_membres.csv") -> pd.DataFrame:
    script_dir = Path(__file__).parent
    csv_path = script_dir.parent / "source" / filename
    df = pd.read_csv(csv_path, sep=",")
    return df[["siren"]].drop_duplicates().rename(columns={"siren": "epci_id"})


def clean_and_prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    df = (df.rename(
            columns={"Code": "epci_id",
                    "Part des jeunes non insérés (ni en emploi, ni scolarisés - NEET) 2022": "value",})
        .drop(columns=["Libellé"])
        )
    
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    # Chargement de la liste exhaustive des EPCI / EPT
    epci_df = fetch_epci_mapping()

    # Left join pour conserver tous les EPCI même sans valeur
    df = epci_df.merge(df, on="epci_id", how="left")
    
    df["indicator_id"] = "i128"
    df["year"] = 2022
    df["unit"] = None
    df["source"] = "Observatoire des territoires"

    return df


def transform_df_to_raw_values(df: pd.DataFrame) -> Iterator[RawValue]:
    for _, row in df.iterrows():
        yield RawValue(
            epci_id=str(row["epci_id"]),
            indicator_id=row["indicator_id"],
            year=int(row["year"]),
            value=float(row["value"]),
            unit=str(row["unit"]),
            source=row["source"],
            meta={}
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

    exists = session.execute(select(Indicator.id).where(Indicator.id == indicator_id)).scalar_one_or_none()
    if not exists:
        raise ValueError(f"L'indicateur {indicator_id} est introuvable en base. Importez d'abord la table de référence.")


def run(csv_filename: str) -> None:
    session = SessionLocal()
    try:
        ensure_indicator_exists(session, "i128") # adapter avec l'indicateur_id
        df = fetch_raw_csv(csv_filename)
        df = clean_and_prepare_df(df)
        rows = list(transform_df_to_raw_values(df))
        if not rows:
            logger.warning("Aucune ligne à insérer")
            return
        count = persist_values(session, rows)
        logger.info("%s lignes upsertées dans valeur_indicateur", count)
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description= "Import CSV -> valeur_indicateur (indicateur i128)") # "Import CSV -> valeur_indicateur"
    parser.add_argument("--csv", default= "i128.csv", help= "Nom du fichier CSV à importer (dans scripts/source/)",) # adapter le default
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
        df = fetch_raw_csv(args.csv)
        df = clean_and_prepare_df(df)
        rows = list(transform_df_to_raw_values(df))
        print(json.dumps([row.__dict__ for row in rows], indent=2, ensure_ascii=False))
        return

    run(args.csv)


if __name__ == "__main__":
    main()
