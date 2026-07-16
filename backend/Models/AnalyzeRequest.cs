namespace DocIntelligenceApi;

// What the Angular app sends: one prompt and a list of files (each with its text).
public class AnalyzeRequest
{
    public string Prompt { get; set; } = "";
    public List<FileInput> Files { get; set; } = new();
}

public class FileInput
{
    public string Name { get; set; } = "";
    public string Text { get; set; } = "";
}
