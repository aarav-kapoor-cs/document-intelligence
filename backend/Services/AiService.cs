using System.Text;
using System.Text.Json;

namespace DocIntelligenceApi;

// This class talks to the AI. It uses the "Ai:Provider" setting in appsettings.json:
//   "mock"   -> a fake answer, no key needed (so the app runs anywhere)
//   "openai" -> OpenAI (test on this laptop)
//   "azure"  -> Azure OpenAI (office laptop)
public class AiService
{
    private readonly IConfiguration _config;
    private readonly HttpClient _http;

    public AiService(IConfiguration config, HttpClient http)
    {
        _config = config;
        _http = http;
    }

    public async Task<AnalyzeResult> AnalyzeAsync(string fileName, string prompt, string documentText)
    {
        string provider = _config["Ai:Provider"] ?? "mock";
        string answer;

        if (provider == "openai")
        {
            answer = await CallOpenAiAsync(prompt, documentText);
        }
        else if (provider == "azure")
        {
            answer = await CallAzureAsync(prompt, documentText);
        }
        else
        {
            string snippet = documentText.Length > 500 ? documentText.Substring(0, 500) : documentText;
            answer =
                "MOCK ANSWER (no AI key used)\n\n" +
                "Prompt: " + prompt + "\n\n" +
                "Text from the document:\n" + snippet;
        }

        return new AnalyzeResult { FileName = fileName, Answer = answer };
    }

    // Call OpenAI. Uses the "OpenAI" settings from the .env file.
    private async Task<string> CallOpenAiAsync(string prompt, string documentText)
    {
        string apiKey = _config["OpenAI:ApiKey"] ?? "";
        string model = _config["OpenAI:Model"] ?? "gpt-4o-mini";

        var payload = new
        {
            model = model,
            messages = new[]
            {
                new { role = "system", content = prompt },
                new { role = "user", content = documentText },
            },
        };

        var request = new HttpRequestMessage(HttpMethod.Post, "https://api.openai.com/v1/chat/completions");
        request.Headers.Add("Authorization", "Bearer " + apiKey);
        request.Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json");

        var response = await _http.SendAsync(request);
        response.EnsureSuccessStatusCode();

        string json = await response.Content.ReadAsStringAsync();
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement
            .GetProperty("choices")[0]
            .GetProperty("message")
            .GetProperty("content")
            .GetString() ?? "";
    }

    // Call Azure OpenAI. Uses the "Azure" settings from the .env file.
    private async Task<string> CallAzureAsync(string prompt, string documentText)
    {
        string endpoint = _config["Azure:Endpoint"] ?? "";
        string apiKey = _config["Azure:ApiKey"] ?? "";
        string deployment = _config["Azure:Deployment"] ?? "";
        string apiVersion = _config["Azure:ApiVersion"] ?? "";
        string url = $"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={apiVersion}";

        var payload = new
        {
            messages = new[]
            {
                new { role = "system", content = prompt },
                new { role = "user", content = documentText },
            },
        };

        var request = new HttpRequestMessage(HttpMethod.Post, url);
        request.Headers.Add("api-key", apiKey);
        request.Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json");

        var response = await _http.SendAsync(request);
        response.EnsureSuccessStatusCode();

        string json = await response.Content.ReadAsStringAsync();
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement
            .GetProperty("choices")[0]
            .GetProperty("message")
            .GetProperty("content")
            .GetString() ?? "";
    }
}
