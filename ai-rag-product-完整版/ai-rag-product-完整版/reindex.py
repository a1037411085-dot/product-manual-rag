"""Build semantic vectors without changing the baseline vectors."""
import argparse
import json
from app import chroma_index, core, postgres, semantic

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product')
    parser.add_argument('--version')
    parser.add_argument('--backend', choices=('chroma', 'semantic'))
    args = parser.parse_args()
    core.init_db()
    backend = args.backend or ('chroma' if postgres.enabled() else 'semantic')
    index = chroma_index.reindex if backend == 'chroma' else semantic.index_chunks
    try:
        result = index(core.connect, args.product, args.version)
    except semantic.EmbeddingNotReady:
        semantic.prepare_local_model()
        result = index(core.connect, args.product, args.version)
    print(json.dumps(result, ensure_ascii=False))
