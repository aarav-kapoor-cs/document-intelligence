using DotNetEnv;
using DocIntelligenceApi;

// Load the keys from the .env file (OpenAI + Azure). Keeps secrets out of appsettings.json.
Env.Load();

var builder = WebApplication.CreateBuilder(args);

// Run on a fixed HTTP address so the Angular app can call it easily.
builder.WebHost.UseUrls("http://localhost:5011");

builder.Services.AddControllers();

// Our small database helper (plain SQL). SQLite file locally, or SQL Server if turned on.
builder.Services.AddSingleton<Database>();

// The service that calls the AI (mock, OpenAI, or Azure).
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
