using System.Drawing;
using System.Reflection;
using TokenTracker.Windows.Core;

namespace TokenTracker.Windows;

internal sealed class ProviderLogoStore : IDisposable
{
    private readonly Dictionary<Provider, Bitmap> logos = new();

    public Image? MenuLogo(Provider provider)
    {
        if (!logos.TryGetValue(provider, out var logo))
        {
            logo = LoadLogo(provider);
            logos[provider] = logo;
        }

        // ToolStripItem does not own or dispose its Image. Reuse the store-owned
        // bitmap across rebuilt menus instead of leaking one clone per provider
        // on every refresh and right-click.
        return logo;
    }

    public void Dispose()
    {
        foreach (var logo in logos.Values)
        {
            logo.Dispose();
        }
    }

    private static Bitmap LoadLogo(Provider provider)
    {
        var resourceName = provider == Provider.Claude
            ? "TokenTracker.Windows.Resources.claudeTemplate.png"
            : "TokenTracker.Windows.Resources.codexTemplate.png";

        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName);
        if (stream is null)
        {
            return FallbackLogo(provider);
        }

        using var image = Image.FromStream(stream);
        return TintToMenuText(image);
    }

    private static Bitmap TintToMenuText(Image source)
    {
        var scaled = new Bitmap(18, 18);
        using (var graphics = Graphics.FromImage(scaled))
        {
            graphics.Clear(Color.Transparent);
            graphics.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
            graphics.DrawImage(source, new Rectangle(1, 1, 16, 16));
        }

        var textColor = SystemColors.MenuText;
        for (var y = 0; y < scaled.Height; y++)
        {
            for (var x = 0; x < scaled.Width; x++)
            {
                var pixel = scaled.GetPixel(x, y);
                if (pixel.A == 0)
                {
                    continue;
                }

                scaled.SetPixel(x, y, Color.FromArgb(pixel.A, textColor.R, textColor.G, textColor.B));
            }
        }

        return scaled;
    }

    private static Bitmap FallbackLogo(Provider provider)
    {
        var bitmap = new Bitmap(18, 18);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.Clear(Color.Transparent);
        using var font = new Font(FontFamily.GenericSansSerif, 9, FontStyle.Bold, GraphicsUnit.Pixel);
        using var brush = new SolidBrush(SystemColors.MenuText);
        var label = provider == Provider.Codex ? "C" : "Cl";
        graphics.DrawString(label, font, brush, 1, 3);
        return bitmap;
    }
}
