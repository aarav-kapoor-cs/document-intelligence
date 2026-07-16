using Microsoft.AspNetCore.Mvc;

namespace DocIntelligenceApi;

[ApiController]
[Route("api")]
public class AnalyzeController : ControllerBase
{
    private readonly Database _db;
    private readonly OcrService _ocr;
    private readonly AiService _ai;

    public AnalyzeController(Database db, OcrService ocr, AiService ai)
    {
        _db = db;
        _ocr = ocr;
        _ai = ai;
    }

    // POST /api/analyze
    // For each file: read its text with OCR, ask the AI, save it, and return the answers.
    [HttpPost("analyze")]
    public async Task<IActionResult> Analyze([FromBody] AnalyzeRequest request)
    {
        List<AnalyzeResult> results = new();

        foreach (FileInput file in request.Files)
        {
            // 1. Turn the base64 text back into the real file bytes.
            byte[] fileBytes = Convert.FromBase64String(file.Base64);

            // 2. Read the text out of the file using Azure OCR.
            string documentText = await _ocr.ReadTextAsync(fileBytes);

            // 3. Ask the AI the prompt about that text.
            AnalyzeResult result = await _ai.AnalyzeAsync(file.Name, request.Prompt, documentText);
            results.Add(result);

            // 4. Save everything to the database.
            _db.Save(file.Name, request.Prompt, documentText, result.Answer);
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
