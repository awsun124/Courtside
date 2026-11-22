"""Check that a model run uses the reviewed expectations and grading code."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
FOLDER=Path(__file__).parent
FREEZE=FOLDER/'rubric_freeze.json'
GRADING_FILES=('courtside/evals/run_news.py','courtside/evals/compare_model.py','courtside/evals/rubrics.py')
DATASETS=('news_cases.json','news_cases_hard.json','news_cases_real.json')


def verify_rubrics(cases_path,freeze_file=FREEZE):
    reviewed=json.loads(Path(freeze_file).read_text())
    path=Path(cases_path).resolve()
    if path.parent!=FOLDER.resolve() or path.name not in DATASETS:
        raise ValueError('This dataset has not had its rubric reviewed')
    for name in GRADING_FILES:
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=reviewed['grading_code_sha256'][name]:
            raise ValueError('Grading code changed after review; review and freeze it again')
    for name in DATASETS:
        if hashlib.sha256((FOLDER/name).read_bytes()).hexdigest()!=reviewed['datasets'][name]['sha256']:
            raise ValueError('Case expectations changed after review; review and freeze them again')
    return dict(version=reviewed['version'],manifest_sha256=hashlib.sha256(Path(freeze_file).read_bytes()).hexdigest(),
                status='matches_reviewed_rubrics',reviewer=reviewed['reviewer'],independent_human_review=False)
