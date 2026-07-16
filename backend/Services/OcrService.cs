using System.Text;
using System.Text.Json;

namespace DocIntelligenceApi;

// Reads the text out of a file (PDF or image) using Azure AI Document Intelligence,
// the "prebuilt-read" OCR model. This works for normal PDFs, scanned PDFs, and images.
// Settings come from the .env file (the "DocumentIntelligence" values).
public class OcrService
{
    private readonly IConfiguration _config;
    private readonly HttpClient _http;

    public OcrService(IConfiguration config, HttpClient http)
    {
        _config = config;
        _http = http;
    }

    // Give it the file's bytes; it returns all the text found in the file.
    public async Task<string> ReadTextAsync(byte[] fileBytes)
    {
        string endpoint = (_config["DocumentIntelligence:Endpoint"] ?? "").TrimEnd('/');
        string apiKey = _config["DocumentIntelligence:ApiKey"] ?? "";
        string apiVersion = _config["DocumentIntelligence:ApiVersion"] ?? "2024-11-30";

        // If OCR is not set up yet (endpoint still a placeholder), return empty text
        // so the app still runs instead of crashing.
        if (!endpoint.StartsWith("http"))
        {
            return "";
        }

        // Step 1: send the file to Azure. It replies with a link to check for the result.
        string startUrl = $"{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze?api-version={apiVersion}";
        string base64 = Convert.ToBase64String(fileBytes);
        string startBody = JsonSerializer.Serialize(new { base64Source = base64 });

        var startRequest = new HttpRequestMessage(HttpMethod.Post, startUrl);
        startRequest.Headers.Add("Ocp-Apim-Subscription-Key", apiKey);
        startRequest.Content = new StringContent(startBody, Encoding.UTF8, "application/json");

        var startResponse = await _http.SendAsync(startRequest);
        if (!startResponse.IsSuccessStatusCode)
        {
            string error = await startResponse.Content.ReadAsStringAsync();
            throw new Exception($"Azure OCR returned {(int)startResponse.StatusCode}. " +
                "Check DocumentIntelligence__Endpoint and DocumentIntelligence__ApiKey in backend/.env. " +
                "Azure said: " + error);
        }

        // Azure does not answer right away. It gives us a link to keep checking.
        string resultUrl = startResponse.Headers.GetValues("Operation-Location").First();

        // Step 2: check that link every second until the OCR is finished.
        for (int i = 0; i < 30; i++)
        {
            await Task.Delay(1000);

            var pollRequest = new HttpRequestMessage(HttpMethod.Get, resultUrl);
            pollRequest.Headers.Add("Ocp-Apim-Subscription-Key", apiKey);

            var pollResponse = await _http.SendAsync(pollRequest);
            pollResponse.EnsureSuccessStatusCode();

            string json = await pollResponse.Content.ReadAsStringAsync();
            using var doc = JsonDocument.Parse(json);
            string status = doc.RootElement.GetProperty("status").GetString() ?? "";

            if (status == "succeeded")
            {
                // "content" is all the text Azure read from the document.
                return doc.RootElement
                    .GetProperty("analyzeResult")
                    .GetProperty("content")
                    .GetString() ?? "";
            }
            if (status == "failed")
            {
                return "";
            }
        }

        return ""; // gave up after about 30 seconds
    }
}
