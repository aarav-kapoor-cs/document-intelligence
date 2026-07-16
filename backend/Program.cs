using DotNetEnv;
using DocIntelligenceApi;

// Load the keys from the .env file (Azure OpenAI + Azure OCR + SQL Server).
// Keeps secrets out of appsettings.json (which is safe to share).
Env.Load();

var builder = WebApplication.CreateBuilder(args);

// Run on a fixed HTTP address so the Angular app can call it easily.
builder.WebHost.UseUrls("http://localhost:5011");

// Allow larger uploads: files are sent as base64, which is bigger than the original file.
builder.WebHost.ConfigureKestrel(options => options.Limits.MaxRequestBodySize = 100 * 1024 * 1024);

builder.Services.AddControllers();

// Our small database helper (plain SQL). SQLite file locally, or SQL Server if turned on.
builder.Services.AddSingleton<Database>();

// The service that reads text out of files (Azure OCR / Document Intelligence).
builder.Services.AddHttpClient<OcrService>();

// The service that calls the AI (mock or Azure OpenAI).
builder.Services.AddHttpClient<AiService>();

// Allow the Angular app (http://localhost:4200) to call this API from the browser.
builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy =>
        policy.WithOrigins("http://localhost:4200").AllowAnyHeader().AllowAnyMethod());
});

var app = builder.Build();

// Make the database table the first time (if it does not exist yet).
app.Services.GetRequiredService<Database>().EnsureTable();

app.UseCors();
app.MapControllers();

app.Run();
