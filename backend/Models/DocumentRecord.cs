namespace DocIntelligenceApi;

// One saved row from the Documents table.
public class DocumentRecord
{
    public int Id { get; set; }
    public string FileName { get; set; } = "";
    public string Prompt { get; set; } = "";
    public string DocumentText { get; set; } = "";
    public string Answer { get; set; } = "";
    public string CreatedAt { get; set; } = "";
}
