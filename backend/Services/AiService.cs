using System.Text;
using System.Text.Json;

namespace DocIntelligenceApi;

// This class talks to the AI. It uses the "Ai:Provider" setting:
//   "mock"  -> a fake answer, no key needed (so the app runs with nothing set up)
//   "azure" -> Azure OpenAI (the real answer, using the "Azure" values from .env)
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

        if (provider == "azure")
        {
            answer = await CallAzureAsync(prompt, documentText);
        }
        else
        {
            string snippet = documentText.Length > 500 ? documentText.Substring(0, 500) : documentText;
            answer =
                "MOCK ANSWER (no AI key used)\n\n" +
                "Prompt: " + prompt + "\n\n" +
                "Text read from the document:\n" + snippet;
        }

        return new AnalyzeResult { FileName = fileName, Answer = answer };
    }

    // Call Azure OpenAI. Uses the "Azure" settings from the .env file.
    // We send the prompt as the instruction and the document text as the content to read.
    private async Task<string> CallAzureAsync(string prompt, string documentText)
    {
        string endpoint = (_config["Azure:Endpoint"] ?? "").TrimEnd('/');
        string apiKey = _config["Azure:ApiKey"] ?? "";
        string deployment = _config["Azure:Deployment"] ?? "";
        string apiVersion = _config["Azure:ApiVersion"] ?? "2024-10-21";
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
        if (!response.IsSuccessStatusCode)
        {
            string error = await response.Content.ReadAsStringAsync();
            throw new Exception($"Azure OpenAI returned {(int)response.StatusCode}. " +
                "Check Azure__Endpoint, Azure__ApiKey, and Azure__Deployment in backend/.env. " +
                "Azure said: " + error);
        }

        string json = await response.Content.ReadAsStringAsync();
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement
            .GetProperty("choices")[0]
            .GetProperty("message")
            .GetProperty("content")
            .GetString() ?? "";
    }
}
