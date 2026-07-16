namespace DocIntelligenceApi;

// What we send back for each file.
public class AnalyzeResult
{
    public string FileName { get; set; } = "";
    public string Answer { get; set; } = "";
}
