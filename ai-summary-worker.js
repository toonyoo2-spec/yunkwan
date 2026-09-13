// ai-summary-worker.js
// 배포: wrangler deploy
// 필요한 설정:
//   1) wrangler secret put ANTHROPIC_API_KEY   (Claude API 키를 Worker에만 저장, 프론트엔드에는 절대 노출 안 됨)
//   2) wrangler.toml의 vars에 ALLOWED_ORIGIN = "https://yunkwan.cloud" (또는 실제 도메인) 설정
//
// 프론트(routine.html)는 이 Worker의 URL로만 요청을 보내고,
// 실제 Anthropic API 키는 이 Worker 안에서만 사용됩니다.

export default {
  async fetch(request, env) {
    const corsHeaders = {
      "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: corsHeaders });
    }

    try {
      const { memo } = await request.json();
      if (!memo || !memo.trim()) {
        return new Response(JSON.stringify({ error: "memo가 비어있습니다" }), {
          status: 400,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
      }

      const apiRes = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": env.ANTHROPIC_API_KEY,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: "claude-sonnet-5",
          max_tokens: 1000,
          messages: [
            {
              role: "user",
              content:
                "다음은 하루 동안 업무 중 남긴 메모입니다. 이걸 간결한 업무일지 형태로 정리해줘. " +
                "불필요한 서두 없이 핵심 항목 위주 불릿 리스트로:\n\n" + memo,
            },
          ],
        }),
      });

      const data = await apiRes.json();
      const summary = (data.content || [])
        .filter((b) => b.type === "text")
        .map((b) => b.text)
        .join("\n");

      return new Response(JSON.stringify({ summary }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: String(err) }), {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }
  },
};
