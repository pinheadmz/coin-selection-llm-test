# Official evaluation environment for P1.1.
#   docker build -t p11 .
#   docker run --rm --network none --cpus 1 --memory 512m -v "$PWD/my_wallet:/sub" p11 /sub
FROM rust:1-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 g++ \
 && rm -rf /var/lib/apt/lists/*
COPY evaluator /challenge/evaluator
COPY scenarios /challenge/scenarios
WORKDIR /challenge
ENTRYPOINT ["python3", "/challenge/evaluator/evaluate.py"]
