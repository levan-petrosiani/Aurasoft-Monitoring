using UglyToad.PdfPig;
using UglyToad.PdfPig.DocumentLayoutAnalysis.TextExtractor;

namespace ApiTestTool.Standards;

/// <summary>Text lines of a PDF, page by page, in reading order.</summary>
public static class PdfText
{
    public static IEnumerable<(int Page, string Line)> Lines(string path)
    {
        using var pdf = PdfDocument.Open(path);
        foreach (var page in pdf.GetPages())
        {
            var text = ContentOrderTextExtractor.GetText(page);
            foreach (var line in text.Split('\n'))
            {
                var trimmed = line.Trim();
                if (trimmed.Length > 0)
                    yield return (page.Number, trimmed);
            }
        }
    }
}
