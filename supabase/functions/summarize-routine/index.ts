// routine.html의 "✨ AI로 요약 정리하기" 버튼이 호출하는 함수.
// 하루 동안 쌓아둔 업무 메모를 받아서 간결한 업무일지 형태로 정리해 돌려준다.
//
// API 키는 Supabase 시크릿(GEMINI_API_KEY)에만 있고 프론트엔드에는 노출되지 않는다.
// analyze-spending과 같은 키·같은 모델을 쓴다.

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const MODEL = "gemini-3.5-flash-lite";
const MAX_MEMO_LENGTH = 8000;
const MAX_ATTEMPTS = 3;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS_HEADERS });
  }

  try {
    const { memo } = await req.json();

    if (typeof memo !== "string" || !memo.trim()) {
      return json({ error: "memo가 비어있어요." }, 400);
    }
    if (memo.length > MAX_MEMO_LENGTH) {
      return json({ error: `메모가 너무 길어요. (${MAX_MEMO_LENGTH}자 이내)` }, 400);
    }

    const apiKey = Deno.env.get("GEMINI_API_KEY");
    if (!apiKey) throw new Error("GEMINI_API_KEY 시크릿이 설정되어 있지 않아요.");

    const prompt = `다음은 하루 동안 업무 중 남긴 메모야. 이걸 간결한 업무일지 형태로 정리해줘.
- 불필요한 서두나 맺음말 없이 핵심 항목 위주의 불릿 리스트로만 답해.
- 각 줄은 "• "로 시작하고, 한 줄에 한 항목씩.
- 메모에 없는 내용을 지어내지 마.
- 비슷한 내용은 한 줄로 합쳐줘.

메모:
${memo}`;

    // 503(UNAVAILABLE)·429(과부하성)는 일시적인 경우가 많아 짧게 재시도한다.
    let geminiRes: Response | null = null;
    let lastErrText = "";
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      const res = await fetch(
        `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${apiKey}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            contents: [{ parts: [{ text: prompt }] }],
            generationConfig: { temperature: 0.3 },
          }),
        },
      );

      if (res.ok) {
        geminiRes = res;
        break;
      }

      lastErrText = await res.text();
      const retryable = res.status === 503 || res.status === 429;
      if (!retryable) {
        throw new Error(`Gemini API 오류 (${res.status}): ${lastErrText}`);
      }
      await new Promise((r) => setTimeout(r, attempt * 1000));
    }

    if (!geminiRes) throw new Error(`Gemini API 오류: ${lastErrText}`);

    const geminiData = await geminiRes.json();
    const summary = (geminiData?.candidates?.[0]?.content?.parts?.[0]?.text || "").trim();

    if (!summary) throw new Error("요약 결과가 비어 있어요. 메모를 조금 더 자세히 적어주세요.");

    return json({ summary });
  } catch (err) {
    console.error("summarize-routine error:", err);
    return json({ error: (err as Error).message }, 500);
  }
});
