using Microsoft.AspNetCore.Mvc;

namespace DocIntelligenceApi;

[ApiController]
[Route("api")]
public class AnalyzeController : ControllerBase
{
    private readonly Database _db;
    private readonly AiService _ai;

    public AnalyzeController(Database db, AiService ai)
    {
        _db = db;
        _ai = ai;
    }

    // POST /api/analyze
    // Analyze each file with the AI, save it to the database, and return the answers.
    [HttpPost("analyze")]
    public async Task<IActionResult> Analyze([FromBody] AnalyzeRequest request)
    {
        List<AnalyzeResult> results = new();

        foreach (FileInput file in request.Files)
        {
            AnalyzeResult result = await _ai.AnalyzeAsync(file.Name, request.Prompt, file.Text);
            results.Add(result);
            _db.Save(file.Name, request.Prompt, file.Text, result.Answer);
        }

        return Ok(new { results });
    }

    // GET /api/analyses
    // Return everything saved so far (newest first) so you can see the data.
    [HttpGet("analyses")]
    public IActionResult GetAll()
    {
        return Ok(_db.GetAll());
    }
}
