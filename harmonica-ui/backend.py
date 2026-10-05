"""CLI adapter: no shell interpolation and no invented HarmoniCA predictions."""
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import pandas as pd

COLUMNS = ['construct', 'questionnaire', 'item_id', 'item_text']


def parse_items(data):
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding='utf-8-sig')
    except Exception as exc:
        raise ValueError(f'Cannot read CSV: {exc}') from exc
    df.columns = df.columns.str.strip()
    if df.columns.duplicated().any():
        raise ValueError('Column names must be unique.')
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError('Missing columns: ' + ', '.join(sorted(missing)))
    df = df[COLUMNS].copy()
    for col in COLUMNS:
        df[col] = df[col].str.strip()
    df['construct'] = df['construct'].str.lower()
    if df.empty:
        raise ValueError('The CSV contains no items.')
    if len(df) > 10000:
        raise ValueError('Please use at most 10,000 items per run.')
    bad = df.eq('').any(axis=1)
    if bad.any():
        raise ValueError('Required values are empty on CSV line(s): ' + ', '.join(str(i+2) for i in df.index[bad][:10]))
    if df.duplicated(['construct', 'questionnaire', 'item_id']).any():
        raise ValueError('Duplicate item IDs within the same questionnaire and construct. Resolve them before running.')
    return df


def run_harmonica(df, force=False, timeout=1800):
    import sys
    root = Path(__file__).resolve().parent
    cache = Path(os.environ.get('HARMONICA_MODELS_DIR', str(Path.home()/'.cache'/'harmonica-ui'/'models'))).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='harmonica-ui-') as directory:
        work = Path(directory)
        source, target, inventory = work/'items.csv', work/'results.csv', work/'inventory.csv'
        df.to_csv(source, index=False)
        shutil.copyfile(root/'assets'/'reference_inventory.csv', inventory)
        command = [sys.executable, str(root/'engine_worker.py'), str(source), str(target), str(inventory), str(cache), '1' if force else '0']
        try:
            proc = subprocess.run(command, cwd=work, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'Run exceeded {timeout // 60} minutes. Reduce the batch or increase the timeout.') from exc
        log = (proc.stdout + '\n' + proc.stderr).strip()
        if proc.returncode:
            raise RuntimeError(f'HarmoniCA exited with code {proc.returncode}.\n{log[-12000:]}')
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError('HarmoniCA produced no result CSV.\n' + log[-12000:])
        data = target.read_bytes()
        results = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
        required = {'construct','questionnaire','item_id','item_text','dimension','dimension_label','confidence'}
        if not required.issubset(results.columns):
            raise RuntimeError('The installed engine returned an incompatible output schema.')
        keys=['construct','questionnaire','item_id']
        if len(results)!=len(df) or set(map(tuple,results[keys].values)) != set(map(tuple,df[keys].values)):
            raise RuntimeError('Output items do not match submitted items. Results were rejected.')
        return data, results, log
