name: Issue triage (label + prefix + conservative retitle + version questions)

on:
  issues:
    types: [opened]

permissions:
  issues: write
  models: read

jobs:
  triage:
    runs-on: ubuntu-latest

    steps:
      - name: Classify + label + title + check versions
        id: decide
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_EVENT_PATH: ${{ github.event_path }}
        run: |
          python3 - <<'PY'
          import os, json, re, urllib.request

          with open(os.environ["GITHUB_EVENT_PATH"], "r", encoding="utf-8") as f:
            event = json.load(f)

          issue = event["issue"]
          original_title = (issue.get("title") or "").strip()
          body = (issue.get("body") or "").strip()

          # Recognize our title prefixes
          prefix_re = re.compile(r'^\[(Bug|New feature request|Locale request)\]\s*', re.I)
          stripped_title = prefix_re.sub("", original_title).strip() or original_title

          # ---------- Robust JSON parse ----------
          def safe_parse_model_json(content: str):
            s = (content or "").strip()
            # Remove ```json fences
            s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.I)
            s = re.sub(r"\s*```\s*$", "", s)

            start = s.find("{")
            end = s.rfind("}")
            if start == -1 or end == -1 or end <= start:
              return None, f"No JSON braces found. Raw:\n{s}"

            candidate = s[start:end+1].strip()
            try:
              return json.loads(candidate), None
            except Exception as e:
              return None, f"JSON parse failed: {e}\nCandidate:\n{candidate}\nRaw:\n{s}"
          # --------------------------------------

          # ---------- Conservative "vague title" heuristic ----------
          def is_vague_title(t: str) -> bool:
            tl = (t or "").strip().lower()
            if not tl:
              return True

            # Purely generic
            generic_exact = {
              "bug","issue","problem","help","question","error","crash",
              "broken","not working","doesn't work","does not work","doesnt work",
              "please help","urgent"
            }
            if tl in generic_exact:
              return True

            tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", tl)
            wc = len(tokens)

            # 1-word titles are almost always vague (unless it's clearly a locale like "da-DK")
            if wc <= 1:
              if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", (t or "").strip()):
                return False
              return True

            # Very short titles can still be fine if they contain clear failure language
            failure_markers = ("not work", "doesn't", "does not", "fails", "failing", "error", "crash", "broken", "cannot", "can't")
            if wc <= 2 and not any(m in tl for m in failure_markers):
              return True

            # Otherwise: assume not vague
            return False
          # --------------------------------------

          def clean(s: str) -> str:
            s = re.sub(r"\s+", " ", (s or "").strip())
            return s[:120].rstrip() if len(s) > 120 else s

          # ---------- LLM: classify + decide rewrite conservatively ----------
          messages = [
            {
              "role": "system",
              "content": (
                "You are a GitHub triage bot.\n"
                "Tasks:\n"
                "1) Classify the issue into exactly one category:\n"
                "   - bug: broken behavior, error, regression, unexpected result.\n"
                "   - feature: request for new functionality or enhancement.\n"
                "   - locale: request to add/translate language/locale/i18n.\n\n"
                "2) Decide whether the user title should be rewritten.\n"
                "Be conservative: ONLY rewrite if the title is clearly misleading, off-topic, or too vague to be useful.\n"
                "If the title is reasonably descriptive (even with imperfect grammar), do NOT rewrite.\n\n"
                "Return ONLY valid JSON with keys:\n"
                "category: one of [bug, feature, locale]\n"
                "rewrite: boolean (true ONLY if misleading/off-topic/vague)\n"
                "rewrite_reason: one of [none, vague, misleading, off_topic]\n"
                "suggested_title: string (ONLY if rewrite=true; otherwise empty string)\n"
                "locale: string (ONLY if category=locale and the requested language/locale is clear; otherwise empty string)\n\n"
                "Rules for suggested_title:\n"
                "- plain text, <= 120 chars, no markdown.\n"
                "- preserve the user's wording when possible; avoid unnecessary paraphrasing.\n"
                "- if category=locale and language is clear, suggested_title should be that language/locale.\n"
              )
            },
            {
              "role": "user",
              "content": f"User title: {stripped_title}\n\nIssue body:\n{body}"
            }
          ]

          payload = {
            "model": "openai/gpt-4.1",
            "messages": messages,
            "temperature": 0,
            # If your endpoint supports it, uncomment:
            # "response_format": {"type": "json_object"},
          }

          req = urllib.request.Request(
            "https://models.github.ai/inference/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
              "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
              "Content-Type": "application/json",
              "Accept": "application/vnd.github+json",
              "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
          )

          with urllib.request.urlopen(req) as r:
            data = json.load(r)

          content = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()

          obj, err = safe_parse_model_json(content)
          if obj is None:
            print("WARN: Could not parse model output as JSON.")
            print(err)
            obj = {}

          category = (obj.get("category") or "").strip().lower()
          rewrite = bool(obj.get("rewrite"))
          rewrite_reason = (obj.get("rewrite_reason") or "none").strip().lower()
          suggested_title = (obj.get("suggested_title") or "").strip()
          locale = (obj.get("locale") or "").strip()

          if category not in {"bug", "feature", "locale"}:
            category = "bug"

          # Heuristic force-rewrite only for truly vague titles
          locally_vague = is_vague_title(stripped_title)
          if locally_vague:
            rewrite = True
            if rewrite_reason == "none":
              rewrite_reason = "vague"

          # If model says "vague" but local heuristic says NOT vague, trust local and keep title
          if rewrite and rewrite_reason == "vague" and not locally_vague:
            rewrite = False

          # Your exact label names:
          label_map = {
            "bug": "bug",
            "feature": "New feature",
            "locale": "New locale",
          }
          label = label_map[category]

          # Prefixes you want in titles:
          prefix_map = {
            "bug": "[Bug]",
            "feature": "[New feature request]",
            "locale": "[Locale request]",
          }
          prefix = prefix_map[category]

          # Build base title text:
          if category == "locale":
            # Prefer explicit locale if detected; otherwise keep user's stripped title
            base_title = clean(locale) if locale else clean(stripped_title)
          else:
            if rewrite:
              base_title = clean(suggested_title) or clean(stripped_title)
            else:
              base_title = clean(stripped_title)

          candidate_title = f"{prefix} {base_title}".strip()
          new_title = "" if candidate_title.lower() == original_title.lower() else candidate_title

          # ---------- Version check (bugs only) ----------
          def has_version_for(kind: str, text: str) -> bool:
            """
            Heuristic: find versions near keywords.
            Matches e.g. 1.2, 1.2.3, v1.2.3, 1.2.3-beta.1
            """
            version_pat = r"(?:v)?\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z\.-]+)?"
            t = text or ""
            if kind == "card":
              # includes Danish "kort" just in case
              kw = r"(?:\bcard\b|\bkort\b)"
            else:
              kw = r"(?:\bsync\b(?:\s+integration)?|\bsync-integration\b|\bsync\s+integration\b)"

            p1 = re.compile(rf"{kw}[^\n]{{0,120}}\b({version_pat})\b", re.I)
            p2 = re.compile(rf"\b({version_pat})\b[^\n]{{0,80}}{kw}", re.I)
            return bool(p1.search(t) or p2.search(t))

          comment_body = ""
          if category == "bug":
            text = f"{stripped_title}\n\n{body}"
            card_ok = has_version_for("card", text)
            sync_ok = has_version_for("sync", text)

            missing_card = not card_ok
            missing_sync = not sync_ok

            if missing_card or missing_sync:
              lines = [
                "Hi! To help us reproduce this bug faster, could you please share the following version info:",
                ""
              ]
              if missing_card:
                lines.append("- **Card version** (e.g. 1.2.3)")
              if missing_sync:
                lines.append("- **Sync integration version** (e.g. 0.9.1)")
              lines += [
                "",
                "Please reply here with the missing details — thank you 🙏"
              ]
              comment_body = "\n".join(lines)
          # ----------------------------------------------

          # Outputs (multiline-safe)
          out = os.environ["GITHUB_OUTPUT"]
          with open(out, "a", encoding="utf-8") as f:
            f.write(f"category={category}\n")
            f.write("label<<EOF\n" + (label or "") + "\nEOF\n")
            f.write("new_title<<EOF\n" + (new_title or "") + "\nEOF\n")
            f.write("comment_body<<EOF\n" + (comment_body or "") + "\nEOF\n")
          PY

      - name: Apply label + title
        uses: actions/github-script@v7
        env:
          LABEL: ${{ steps.decide.outputs.label }}
          NEW_TITLE: ${{ steps.decide.outputs.new_title }}
        with:
          script: |
            const owner = context.repo.owner;
            const repo = context.repo.repo;
            const issue_number = context.payload.issue.number;

            const labelName = process.env.LABEL?.trim();
            const newTitle = process.env.NEW_TITLE?.trim();

            // Add label (assumes it exists; if it doesn't, GitHub will error)
            if (labelName) {
              await github.rest.issues.addLabels({
                owner, repo, issue_number,
                labels: [labelName],
              });
            }

            // Update title (prefix always enforced; rewrite only if needed)
            if (newTitle) {
              await github.rest.issues.update({
                owner, repo, issue_number,
                title: newTitle,
              });
            }

      - name: Ask for missing versions (bug only)
        if: steps.decide.outputs.comment_body != ''
        uses: actions/github-script@v7
        env:
          COMMENT: ${{ steps.decide.outputs.comment_body }}
        with:
          script: |
            await github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.payload.issue.number,
              body: process.env.COMMENT
            });
