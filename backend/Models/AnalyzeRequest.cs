namespace DocIntelligenceApi;

// What the Angular app sends: one prompt and a list of files.
// Each file is sent as base64 text (the raw file bytes), so the backend can OCR it.
public class AnalyzeRequest
{
    public string Prompt { get; set; } = "";
    public List<FileInput> Files { get; set; } = new();
}

public class FileInput
{
    public string Name { get; set; } = "";
    public string Base64 { get; set; } = "";
}
