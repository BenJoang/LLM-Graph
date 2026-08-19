import os
import sys
from pathlib import Path

import uvicorn

BASE_DIR = Path(__file__).resolve().parents[2]

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
    
def main() -> None:
    uvicorn.run(
        "src.api.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("LLM_GRAPH_GUI_PORT", "8100")),
        reload=False,
        log_level="info",
    )

if __name__ == "__main__":
    main()
