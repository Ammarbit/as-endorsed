# Deploying

The public instance runs on Azure Container Apps: https://as-endorsed.wittybay-fdf1bbec.germanywestcentral.azurecontainerapps.io

## Azure Container Apps (what the live instance uses)

Built in the cloud from the repo source, no local Docker needed. Scale-to-zero keeps idle cost at nothing; the consumption plan's free grant covers a demo's traffic, and the Basic container registry that holds the image is the one small fixed line item.

```bash
az group create -n as-endorsed-rg -l germanywestcentral
az containerapp up --name as-endorsed --resource-group as-endorsed-rg --location germanywestcentral \
  --environment as-endorsed-env --source . --ingress external --target-port 8000 \
  --env-vars PORT=8000 AS_ENDORSED_DATA_DIR=/data FASTEMBED_CACHE_PATH=/data/models
az containerapp update -n as-endorsed -g as-endorsed-rg --cpu 1.0 --memory 2.0Gi --min-replicas 0 --max-replicas 1
az containerapp secret set -n as-endorsed -g as-endorsed-rg --secrets anthropic-key=<key>      # optional
az containerapp update -n as-endorsed -g as-endorsed-rg --set-env-vars ANTHROPIC_API_KEY=secretref:anthropic-key
```

Redeploy after a change with the same `az containerapp up ... --source .` command.

The image is self-contained (see the Dockerfile): forms, parsed trees, synthetic accounts, extractions, resolutions and ONNX models are baked in at build time, so any host that runs a Docker image with about 2 GB of RAM works. Runtime needs no network unless `ANTHROPIC_API_KEY` is set.

## Hugging Face Spaces (free, public URL, sleeps when idle)

1. Create a Space at https://huggingface.co/new-space with SDK **Docker**, hardware **CPU basic**.
2. Log in locally: `pip install huggingface_hub` then `hf auth login` (token from https://huggingface.co/settings/tokens with write access).
3. Push this repo to the Space with the Space's README front matter:

```bash
deploy/push-hf-space.sh <your-hf-username>/as-endorsed
```

The script pushes `main` to the Space with `deploy/hf-space-README.md` in place of the repo README, which is how a Space declares `sdk: docker` and `app_port`. First build takes 10 to 15 minutes (the bootstrap runs inside the build). The Space URL is `https://<user>-as-endorsed.hf.space`.

Optional: add `ANTHROPIC_API_KEY` as a Space secret to enable the Claude generator.

## Fly.io (always-on, ~$5 to $10 a month)

```bash
fly launch --no-deploy --copy-config      # uses fly.toml
fly secrets set ANTHROPIC_API_KEY=...     # optional
fly deploy
```

`fly.toml` mounts a volume at `/data` for the embedding cache; the baked image already contains everything else.

## Any container host (Railway, Render, Azure Container Apps, Cloud Run)

Point it at the published image `ghcr.io/ammarbit/as-endorsed:latest` (built by `.github/workflows/image.yml` on every push to `main`), expose port 8000 (or set `PORT`), give it 2 GB of memory. No volume is required.
