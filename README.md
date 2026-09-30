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

1. `brew install uv gh`. `waldito` brings its own Python packages through uv.
2. Fork this repo (the Fork button), unless you can push to it directly.
3. Make the two tokens below, and load the GitHub one with `gh auth login --with-token`.
4. Run `waldito join` once; it creates a signing key and prints the `identities` entry to add.
5. Open a pull request adding that entry to the run's `plan.yaml`. Plan changes are reviewed by
   the run's organizer (see `CODEOWNERS`), never merged automatically.
6. Set this repo's Watch to Ignore. `waldito join` opens a pull request per unit of work under
   your account, and GitHub emails authors about each merge, even with Watch set to Participating.

## Tokens

`waldito join` needs two narrow tokens and nothing broader. Don't use `gh auth login`'s browser
flow, a classic GitHub token, or a classic Hugging Face token: those reach every repository your
account can, including your employers' and clients'.

### GitHub: fine-grained personal access token

1. github.com > your avatar > Settings > Developer settings > Personal access tokens >
   Fine-grained tokens > Generate new token.
2. Token name: `waldito`. Expiration: whatever you are comfortable renewing (30-90 days).
3. Resource owner: your personal account. A token has one owner, so organizations you belong to
   are out of its reach entirely.
4. Repository access: Only select repositories > your fork of this repo (or this repo itself, if
   you can push to it). No other repositories.
5. Permissions > Repository permissions:
   - Contents: Read and write (push record branches, delete them after merging)
   - Pull requests: Read and write (open record pull requests, see when they merge)
   - Metadata: Read-only (required, added for you)

   Leave every other repository permission and all account permissions at No access.
6. Generate token and copy it. In your own terminal: `gh auth login --with-token`, paste, Ctrl-D.
   `gh auth status` should show a `github_pat_` token.

### Hugging Face: fine-grained access token

1. huggingface.co > your avatar > Settings > Access Tokens > Create new token.
2. Token type: Fine-grained. Token name: `waldito`.
3. User permissions > Repositories:
   - Read access to contents of all repos under your personal namespace
   - Write access to contents/settings of all repos under your personal namespace

   Leave everything else unchecked: inference, webhooks, collections, discussions, billing, and
   every organization permission.
4. Create token and copy it. The first `waldito join` that needs Hugging Face asks for it and
   saves it; no `hf` command is needed.

`waldito join` creates one public model repo per submission or merge under your namespace
(`<you>/waldito-<run>-r<round>-<unit>-<identity>`), and downloads others' public repos, which
needs no token.

Opening a record pull request from a fork with a fine-grained token is not tested yet; so far
only contributors who push to this repo directly have run it.
