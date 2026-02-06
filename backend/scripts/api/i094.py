"""
Indicateur i094 : Indice de fragilité numérique

Source : La Mednum
URL : https://fragilite-numerique.fr/?indicators=no_thd_coverage_rate,no_4g_coverage_rate,poverty_rate,older_65_rate,nscol15p_rate&zoom=7&center=3.3936626281126223,49.46899452192184&division=epci&division_auto=false&selected_territories= 
Dernières données disponibles : 2023

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


def fetch_raw_csv(filename: str, sep=",", header=0) -> pd.DataFrame:
    script_dir = Path(__file__).parent      # scripts/api/
    csv_path = script_dir.parent / "source" / filename  # scripts/source/i094.csv
    return pd.read_csv(csv_path, sep=sep, header=header)


def fetch_epci_mapping(filename: str = "epci_membres.csv") -> pd.DataFrame:
    script_dir = Path(__file__).parent
    csv_path = script_dir.parent / "source" / filename
    df = pd.read_csv(csv_path, sep=",")
    return df[["siren"]].drop_duplicates().rename(columns={"siren": "epci_id"})


def clean_and_prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    df = (df.rename(
            columns={"code": "epci_id",
                    "total": "value",})
        .drop(columns=['name', 'division', 'no_thd_coverage_rate',
       'no_4g_coverage_rate', 'poverty_rate', 'poverty_rate_50_plus',
       'poverty_rate_30_minus', 'library_distance', 'library_distance_and_mjc',
       'public_service_distance', 'public_service_distance_and_ml',
       'menseul_rate', 'menseul_rate_50_plus', 'fammono_rate',
       'fammono_rate_25_minus', 'obstacle_to_mobility_rate',
       'unemployement_rate', 'unemployement_rate_50_plus',
       'unemployement_rate_25_minus', 'foreigners_rate',
       'foreigners_rate_55_plus', 'foreigners_rate_25_minus',
       'social_assistance_rate', 'aah_rate', 'older_65_rate', 'older_75_rate',
       'nscol15p_rate', 'nscol15p_rate_50_plus', 'nscol15p_rate_15_25_year',
       'neet_16_25_year_rate', 'population', 'population_density',
       'area', 'is_zrr', 'number_of_qpv', 'percentage_of_qpv',
       'digital_mediation_center_count', 'france_services_center_count',
       'conseillers_numerique_center_count', 'aidants_connect_center_count',
       'aging_index'])
        )
    
    df = df.drop(index=0)
    df["epci_id"] = df["epci_id"].astype("int64")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    # Chargement de la liste exhaustive des EPCI / EPT
    epci_df = fetch_epci_mapping()

    # Left join pour conserver tous les EPCI même sans valeur
    df = epci_df.merge(df, on="epci_id", how="left")

    df["indicator_id"] = "i094"
    df["year"] = 2023
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
            unit=str(row["unit"] or ""),
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
        ensure_indicator_exists(session, "i094") # adapter avec l'indicateur_id
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
    parser = argparse.ArgumentParser(description= "Import CSV -> valeur_indicateur (indicateur i094)") # "Import CSV -> valeur_indicateur"
    parser.add_argument("--csv", default= "i094.csv", help= "Nom du fichier CSV à importer (dans scripts/source/)",) # adapter le default
    parser.add_argument("--save",
                        action="store_true",
                        help="Output CSV (dans scripts/output/)",
    )
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
