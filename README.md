# waldo-builds
Repo for tracking distributed pretraining work while maintaining provenance 

## How it works

Each training run is a directory, `runs/<name>/`, whose `plan.yaml` sets the data, rounds, and the
identities allowed to contribute. Contributors run `waldito join` from
[mdagosta/waldo](https://github.com/mdagosta/waldo) (`md/waldito` branch):

```
waldito join https://github.com/mdagosta/waldo-builds runs/<name> --identity <you>
```

Each pass trains, verifies, or merges one unit, uploads the weights to the contributor's own
Hugging Face account, and opens a pull request adding a signed record under `rounds/`. The
`records` Action merges record pull requests whose signatures check out and closes the rest;
it never runs code from a pull request. The history of `main` is the record of how each model
was built.

## Joining a run

1. `brew install uv gh` and `gh auth login`. `waldito` brings its own Python packages through uv,
   and asks for a Hugging Face write token the first time it needs one.
2. Run `waldito join` once; it creates a signing key and prints the `identities` entry to add.
3. Open a pull request adding that entry to the run's `plan.yaml`. Plan changes are reviewed by
   the run's organizer (see `CODEOWNERS`), never merged automatically.
