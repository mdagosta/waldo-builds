# waldo-builds
Repo for tracking distributed pretraining work while maintaining provenance

## How it works

Each training run is a directory, `runs/<name>/`, whose `plan.yaml` sets the data, rounds, and the
identities allowed to contribute. Contributors run `waldo model join` from
[mdagosta/waldo](https://github.com/mdagosta/waldo) (`md/model-join` branch):

```
waldo model join https://github.com/mdagosta/waldo-builds runs/<name> --identity <you>
```

Each pass claims a slot, then trains a unit or closes a round, uploads the weights to the
contributor's own Hugging Face account, and opens a pull request adding a signed record under
`rounds/`:

- A claim reserves a replica of a unit, or a closing slot, before the work, so contributors
  divide a round instead of repeating each other. A claim not followed by its record within the
  plan's `claim_minutes` may be retaken.
- A submission records the unit's weights by hash and their lineage back to the round's start.
- A merge records the units it accepted, the ones it dropped and why, and the evidence behind each
  verdict: every replica's held-out loss, measured by the closer on the exact records that unit's
  training held out. Several closers (`closers`, default 3) merge each round independently, and
  the round goes to the merge a majority of them sign, so no one closer is trusted.
- Merged weights are a waldo merge whose records pin every parent, and their lineage travels with
  each published release, so any model built here traces back to the data it was trained on.

The `records` Action merges record pull requests whose signatures and claims check out and closes the rest;
it never runs code from a pull request. The history of `main` is the record of how each model
was built.

## Joining a run

1. Install Go, `uv`, and `gh`, then build waldo:
   ```
   git clone -b md/model-join https://github.com/mdagosta/waldo && cd waldo
   go build -o waldo ./cmd/waldo
   ```
   Training needs a Python with MLX (Apple silicon) or PyTorch (CUDA). `uv` provides it per run:
   `uv run --with mlx --with huggingface_hub ./waldo model join ...`
2. Make a GitHub machine account and its two tokens (below). Fork this repo as that account.
3. Run `waldo model join` once with `--identity <you>`. It creates a signing key in
   `~/.waldo/join/<you>/` and prints the `identities` entry to add.
4. Open a pull request adding that entry, with your Hugging Face user as `hf`, to the run's
   `plan.yaml`. Plan changes are reviewed by the run's organizer (see `CODEOWNERS`), never merged
   automatically.
5. Run join for real, with the machine account's noreply address as the commit author:
   `waldo model join <repo> runs/<name> --identity <you> --email <id>+<account>@users.noreply.github.com`

Everything of an identity lives in `~/.waldo/join/<you>/`: its signing key, GitHub token, clone,
models, and exports, so several identities can share one machine.

## Accounts and tokens

### GitHub: a machine account with a classic `public_repo` token

Record pull requests come from a fork, and GitHub's fine-grained tokens cannot open a pull request
on a repository their owner doesn't control: the push to the fork succeeds, then the pull request
is refused ("Resource not accessible by personal access token"). Join therefore needs a classic
token with `public_repo`, and that scope reaches every public repository its account can write.
Keep it away from your personal, employer, and client repositories by giving it to a machine
account instead. GitHub allows one free machine account per person.

1. Create the machine account (a plus address of your email works, e.g. `you+bot@example.com`).
   In its Settings > Emails, turn on "Keep my email addresses private" and "Block command line
   pushes that expose my email"; note its `<id>+<account>@users.noreply.github.com` address.
2. As the machine account, fork this repo.
3. As the machine account: Settings > Developer settings > Personal access tokens > Tokens
   (classic) > Generate new token. Scope: `public_repo` only. Expiration: whatever you are
   comfortable renewing.
4. Save it for your identity, readable only by you (join refuses a token file others can read):
   ```
   mkdir -p ~/.waldo/join/<you> && chmod 700 ~/.waldo/join/<you>
   umask 077; read -rs -p "token: " t; printf %s "$t" > ~/.waldo/join/<you>/github-token; unset t
   ```
   Join refuses a token file that is a symlink, readable by others, or in a directory others can
   read, and hands it only to its own `gh` and `git` calls, so your personal `gh` login is
   untouched. Several identities on one account each need their own copy.

Records then land on the machine account's contribution graph, not yours: it opens the pull
requests and authors the record commits, and this repo's Action merges them.

### Hugging Face: fine-grained access token

1. huggingface.co > your avatar > Settings > Access Tokens > Create new token.
2. Token type: Fine-grained. Token name: `waldo-join`.
3. User permissions > Repositories:
   - Read access to contents of all repos under your personal namespace
   - Write access to contents/settings of all repos under your personal namespace

   Leave everything else unchecked: inference, webhooks, collections, discussions, billing, and
   every organization permission.
4. Create the token, then log in once with `uv run --with huggingface_hub hf auth login`, or set
   `HF_TOKEN`.

Join creates one public model repo per submission or merge under your namespace
(`<you>/waldo-<run>-r<round>-<unit>-<identity>`) and downloads others' public repos, which needs
no token.

Set this repo's Watch to Ignore: GitHub emails pull request authors about each merge.

## History

`smoke-v1`, `python-basics-v1`, and `bot-test-v1` were recorded by waldito, the Python prototype
of `waldo model join`, and are signed in its `waldito` namespace. `waldo model join` signs and
verifies records in the `waldo-join` namespace.
