.PHONY: install db reseed ingest benchmark evals compare serve test smoke

install:
	python -m pip install -r requirements.txt

db:            ## start MySQL and load schema + seed (first run only)
	docker compose up -d mysql

reseed:        ## reload data so relative dates ("delivered 45 days ago") are true again
	docker compose exec -T mysql mysql -uroot -prootpass < db/schema.sql
	docker compose exec -T mysql mysql -uroot -prootpass < db/seed.sql

ingest:        ## chunk, embed and upsert the policy documents into Pinecone
	python -m rag.ingest

benchmark:     ## Simple vs HyDE vs Multi-Query vs dynamic, as LangSmith experiments
	python -m rag.benchmark

evals:         ## golden dataset through the agent, as a LangSmith experiment
	python -m evals.run_evals

compare:       ## routed vs always-gpt-4o, cost and accuracy side by side
	python -m evals.run_evals --compare-routing

serve:
	uvicorn app.api:app --reload --port 8000

test:          ## offline unit and integration tests (needs MySQL, no API keys)
	python -m pytest -q

smoke:         ## whole eval harness with a fake model, no API keys
	python -m scripts.offline_smoke
