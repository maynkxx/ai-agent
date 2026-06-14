# One-command setup for reviewers (PDF bonus).
#
#   docker build -t uniagent .
#   docker run --rm -e GROQ_API_KEY=$GROQ_API_KEY -v "$PWD/data:/app/data" uniagent
#
# With no GROQ_API_KEY the container still runs and produces partial data via the
# regex-heuristic fallback (graceful degradation). Mounting ./data persists the
# output (output.json, CSVs, universities.db) back to the host.
FROM python:3.11-slim

WORKDIR /app

# Install deps first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project.
COPY . .

# Default: scrape the configured universities and write JSON + CSV to data/.
# Override the command to query, e.g.:
#   docker run --rm uniagent python cli.py list
#   docker run --rm uniagent python eval/evaluate.py
ENTRYPOINT ["python", "cli.py"]
CMD ["run"]
