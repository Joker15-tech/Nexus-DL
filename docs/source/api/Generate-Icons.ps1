# ==============================================================================
#  NexusDL — Generate-Icons.ps1
# ==============================================================================
#  Génère toutes les icônes et images pour le manifest PWA, le favicon,
#  les réseaux sociaux (OG image), et les raccourcis.
#
#  Utilise System.Drawing (natif Windows, aucune dépendance externe).
#
#  Usage :
#      .\scripts\Generate-Icons.ps1
#      .\scripts\Generate-Icons.ps1 -OutputDir "src/nexusdl/interfaces/web/frontend/public"
#      .\scripts\Generate-Icons.ps1 -Verbose
#
#  Prérequis :
#      - Windows PowerShell 5.1+ ou PowerShell 7+
#      - .NET Framework 4.5+ (inclus dans Windows)
#      - Droits d'écriture dans le dossier de sortie
#
#  Copyright (C) 2026 NEXUS-QUANTUM
#  SPDX-License-Identifier: GPL-3.0-or-later
# ==============================================================================

[CmdletBinding()]
param(
    [string]$OutputDir = "src/nexusdl/interfaces/web/frontend/public",
    [string]$SourceLogo = "",           # PNG source optionnel (haute résolution)
    [switch]$SkipScreenshots = $false,  # Ignorer les screenshots placeholder
    [switch]$Force = $false              # Écraser sans demander
)

# ==============================================================================
#  CONFIGURATION
# ==============================================================================

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'

# Charge les assemblies .NET nécessaires
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

# --- Couleurs Nexus Neon ---
$Colors = @{
    NeonCyan    = [System.Drawing.Color]::FromArgb(0, 240, 255)
    NeonMagenta = [System.Drawing.Color]::FromArgb(255, 0, 229)
    NeonPurple  = [System.Drawing.Color]::FromArgb(139, 0, 255)
    NeonLime    = [System.Drawing.Color]::FromArgb(198, 255, 0)
    NeonGreen   = [System.Drawing.Color]::FromArgb(0, 255, 136)
    NeonYellow  = [System.Drawing.Color]::FromArgb(255, 221, 0)
    NeonOrange  = [System.Drawing.Color]::FromArgb(255, 107, 0)
    NeonRed     = [System.Drawing.Color]::FromArgb(255, 0, 64)
    BgVoid      = [System.Drawing.Color]::FromArgb(5, 5, 20)
    BgDeep      = [System.Drawing.Color]::FromArgb(10, 14, 39)
    BgRaised    = [System.Drawing.Color]::FromArgb(19, 26, 58)
    TextLight   = [System.Drawing.Color]::FromArgb(230, 247, 255)
    TextMuted   = [System.Drawing.Color]::FromArgb(163, 184, 214)
}

# --- Dimensions standards PWA ---
$IconSizes = @(72, 96, 128, 144, 152, 192, 384, 512)

# --- Bannières ---
$ProjectName = "NexusDL"
$Tagline = "Universal Manga Downloader"

# ==============================================================================
#  FONCTIONS UTILITAIRES
# ==============================================================================

function Write-Banner {
    param([string]$Text)
    Write-Host ""
    Write-Host ("═" * 70) -ForegroundColor DarkCyan
    Write-Host "  $Text" -ForegroundColor Cyan
    Write-Host ("═" * 70) -ForegroundColor DarkCyan
}

function Write-Step {
    param([string]$Text)
    Write-Host "  ▸ " -ForegroundColor Magenta -NoNewline
    Write-Host $Text -ForegroundColor White
}

function Write-Success {
    param([string]$Text)
    Write-Host "  ✓ " -ForegroundColor Green -NoNewline
    Write-Host $Text -ForegroundColor Gray
}

function Write-Error-Custom {
    param([string]$Text)
    Write-Host "  ✗ " -ForegroundColor Red -NoNewline
    Write-Host $Text -ForegroundColor Gray
}

# ==============================================================================
#  GÉNÉRATION DU LOGO NEXUS NEON
# ==============================================================================

function New-HexagonPath {
    param(
        [float]$CenterX,
        [float]$CenterY,
        [float]$Radius
    )

    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $points = @()
    for ($i = 0; $i -lt 6; $i++) {
        $angle = [Math]::PI / 3 * $i - [Math]::PI / 2
        $x = $CenterX + $Radius * [Math]::Cos($angle)
        $y = $CenterY + $Radius * [Math]::Sin($angle)
        $points += New-Object System.Drawing.PointF($x, $y)
    }
    $path.AddPolygon($points)
    return $path
}

function New-RoundedRectPath {
    param(
        [float]$X,
        [float]$Y,
        [float]$Width,
        [float]$Height,
        [float]$Radius
    )

    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $diameter = $Radius * 2

    $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
    $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
    $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()

    return $path
}

function New-NexusLogo {
    <#
    .SYNOPSIS
    Génère le logo NexusDL avec hexagone, dégradé néon et lettre N.
    #>
    param(
        [int]$Size,
        [System.Drawing.Color]$PrimaryColor = $Colors.NeonCyan,
        [System.Drawing.Color]$SecondaryColor = $Colors.NeonPurple,
        [System.Drawing.Color]$AccentColor = $Colors.NeonMagenta,
        [bool]$Transparent = $false,
        [bool]$Monochrome = $false,
        [double]$Padding = 0.05,          # 5% de padding par défaut
        [bool]$AddGlow = $true
    )

    $bitmap = New-Object System.Drawing.Bitmap($Size, $Size)
    $bitmap.SetResolution(144, 144)

    $g = [System.Drawing.Graphics]::FromImage($bitmap)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality

    try {
        # --- Fond ---
        if ($Transparent) {
            $g.Clear([System.Drawing.Color]::Transparent)
        } else {
            $g.Clear($Colors.BgDeep)
        }

        $center = $Size / 2.0
        $paddingPx = $Size * $Padding
        $logoRadius = ($Size - $paddingPx * 2) / 2.0

        # --- Cercle de fond avec dégradé ---
        if (-not $Transparent -and -not $Monochrome) {
            $bgRect = New-Object System.Drawing.RectangleF(
                $paddingPx, $paddingPx,
                ($Size - $paddingPx * 2), ($Size - $paddingPx * 2)
            )

            $bgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
                $bgRect,
                $Colors.BgVoid,
                $Colors.BgRaised,
                45.0
            )
            $g.FillEllipse($bgBrush, $bgRect)
            $bgBrush.Dispose()
        }

        # --- Lueur externe ---
        if ($AddGlow -and -not $Monochrome) {
            for ($i = 8; $i -ge 1; $i--) {
                $glowAlpha = [int](255 * (1 - $i / 8.0) * 0.15)
                $glowColor = [System.Drawing.Color]::FromArgb(
                    $glowAlpha,
                    $PrimaryColor.R,
                    $PrimaryColor.G,
                    $PrimaryColor.B
                )
                $glowPen = New-Object System.Drawing.Pen($glowColor, ($i * 2))
                $glowPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round

                $glowRadius = $logoRadius * 0.75 + ($i * 2)
                $hexPath = New-HexagonPath -CenterX $center -CenterY $center -Radius $glowRadius
                $g.DrawPath($glowPen, $hexPath)

                $hexPath.Dispose()
                $glowPen.Dispose()
            }
        }

        # --- Hexagone principal ---
        $hexRadius = $logoRadius * 0.75
        $hexPath = New-HexagonPath -CenterX $center -CenterY $center -Radius $hexRadius

        if ($Monochrome) {
            $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, ($Size / 40.0))
            $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
            $g.DrawPath($pen, $hexPath)
            $pen.Dispose()
        } else {
            # Dégradé de l'hexagone
            $hexBounds = $hexPath.GetBounds()
            $hexBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
                $hexBounds,
                $PrimaryColor,
                $AccentColor,
                45.0
            )

            # Contour externe (glow)
            $outerPen = New-Object System.Drawing.Pen(
                [System.Drawing.Color]::FromArgb(100, $PrimaryColor.R, $PrimaryColor.G, $PrimaryColor.B),
                ($Size / 25.0)
            )
            $outerPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
            $g.DrawPath($outerPen, $hexPath)
            $outerPen.Dispose()

            # Contour principal
            $mainPen = New-Object System.Drawing.Pen($hexBrush, ($Size / 45.0))
            $mainPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
            $g.DrawPath($mainPen, $hexPath)
            $mainPen.Dispose()

            $hexBrush.Dispose()
        }

        # --- Lettre "N" stylisée ---
        $fontSize = [float]($hexRadius * 1.1)
        $font = $null

        # Essaie plusieurs polices stylisées
        $fontNames = @("Orbitron", "Rajdhani", "Bahnschrift", "Segoe UI Black", "Arial Black", "Arial")
        foreach ($name in $fontNames) {
            try {
                $font = New-Object System.Drawing.Font($name, $fontSize, [System.Drawing.FontStyle]::Bold)
                if ($font.Name -eq $name -or $font.Name -like "$name*") { break }
                $font.Dispose()
                $font = $null
            } catch {
                continue
            }
        }

        if ($null -eq $font) {
            $font = New-Object System.Drawing.Font("Arial", $fontSize, [System.Drawing.FontStyle]::Bold)
        }

        $stringFormat = New-Object System.Drawing.StringFormat
        $stringFormat.Alignment = [System.Drawing.StringAlignment]::Center
        $stringFormat.LineAlignment = [System.Drawing.StringAlignment]::Center

        $textBounds = New-Object System.Drawing.RectangleF(0, 0, $Size, $Size)

        if ($Monochrome) {
            $textBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
        } else {
            # Texte avec effet néon (double passe)
            $textBrush = New-Object System.Drawing.SolidBrush($Colors.TextLight)
        }

        $g.DrawString("N", $font, $textBrush, $textBounds, $stringFormat)
        $textBrush.Dispose()

        # --- Nœuds aux sommets de l'hexagone ---
        if (-not $Monochrome) {
            $nodeRadius = $Size * 0.025
            for ($i = 0; $i -lt 6; $i++) {
                $angle = [Math]::PI / 3 * $i - [Math]::PI / 2
                $nodeX = $center + $hexRadius * [Math]::Cos($angle)
                $nodeY = $center + $hexRadius * [Math]::Sin($angle)

                # Halo du nœud
                for ($j = 4; $j -ge 1; $j--) {
                    $haloAlpha = [int](255 * (1 - $j / 4.0) * 0.3)
                    $haloColor = [System.Drawing.Color]::FromArgb(
                        $haloAlpha,
                        $SecondaryColor.R,
                        $SecondaryColor.G,
                        $SecondaryColor.B
                    )
                    $haloBrush = New-Object System.Drawing.SolidBrush($haloColor)
                    $g.FillEllipse(
                        $haloBrush,
                        ($nodeX - $nodeRadius - $j * 3),
                        ($nodeY - $nodeRadius - $j * 3),
                        (($nodeRadius + $j * 3) * 2),
                        (($nodeRadius + $j * 3) * 2)
                    )
                    $haloBrush.Dispose()
                }

                # Nœud principal
                $nodeBrush = New-Object System.Drawing.SolidBrush($SecondaryColor)
                $g.FillEllipse(
                    $nodeBrush,
                    ($nodeX - $nodeRadius),
                    ($nodeY - $nodeRadius),
                    ($nodeRadius * 2),
                    ($nodeRadius * 2)
                )
                $nodeBrush.Dispose()
            }
        }

        $hexPath.Dispose()
        $font.Dispose()

    } finally {
        $g.Dispose()
    }

    return $bitmap
}

# ==============================================================================
#  GÉNÉRATION DES ICÔNES DE RACCOURCIS
# ==============================================================================

function New-ShortcutIcon {
    <#
    .SYNOPSIS
    Génère une icône de raccourci (avec symbole au centre).
    #>
    param(
        [int]$Size,
        [string]$Symbol,
        [System.Drawing.Color]$AccentColor
    )

    $bitmap = New-Object System.Drawing.Bitmap($Size, $Size)
    $bitmap.SetResolution(144, 144)

    $g = [System.Drawing.Graphics]::FromImage($bitmap)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    try {
        # Fond arrondi avec dégradé
        $padding = $Size * 0.08
        $rect = New-Object System.Drawing.RectangleF(
            $padding, $padding,
            ($Size - $padding * 2), ($Size - $padding * 2)
        )

        $bgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            $rect,
            $Colors.BgDeep,
            $Colors.BgRaised,
            135.0
        )
        $roundedPath = New-RoundedRectPath -X $rect.X -Y $rect.Y -Width $rect.Width -Height $rect.Height -Radius ($Size * 0.2)
        $g.FillPath($bgBrush, $roundedPath)
        $bgBrush.Dispose()

        # Bordure néon
        $borderPen = New-Object System.Drawing.Pen($AccentColor, [Math]::Max(1, $Size / 40.0))
        $g.DrawPath($borderPen, $roundedPath)
        $borderPen.Dispose()

        # Symbole au centre
        $fontSize = [float]($Size * 0.5)
        $font = $null
        foreach ($name in @("Segoe UI Emoji", "Segoe UI Symbol", "Arial")) {
            try {
                $font = New-Object System.Drawing.Font($name, $fontSize, [System.Drawing.FontStyle]::Bold)
                break
            } catch { continue }
        }

        if ($null -eq $font) {
            $font = New-Object System.Drawing.Font("Arial", $fontSize, [System.Drawing.FontStyle]::Bold)
        }

        $sf = New-Object System.Drawing.StringFormat
        $sf.Alignment = [System.Drawing.StringAlignment]::Center
        $sf.LineAlignment = [System.Drawing.StringAlignment]::Center

        $textBounds = New-Object System.Drawing.RectangleF(0, 0, $Size, $Size)
        $textBrush = New-Object System.Drawing.SolidBrush($AccentColor)
        $g.DrawString($Symbol, $font, $textBrush, $textBounds, $sf)

        $textBrush.Dispose()
        $font.Dispose()
        $roundedPath.Dispose()

    } finally {
        $g.Dispose()
    }

    return $bitmap
}

# ==============================================================================
#  GÉNÉRATION DE L'OG IMAGE
# ==============================================================================

function New-OgImage {
    <#
    .SYNOPSIS
    Génère l'image Open Graph (1200x630) pour les réseaux sociaux.
    #>
    param(
        [int]$Width = 1200,
        [int]$Height = 630
    )

    $bitmap = New-Object System.Drawing.Bitmap($Width, $Height)
    $bitmap.SetResolution(72, 72)

    $g = [System.Drawing.Graphics]::FromImage($bitmap)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    try {
        # Fond dégradé
        $bgRect = New-Object System.Drawing.RectangleF(0, 0, $Width, $Height)
        $bgBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            $bgRect,
            $Colors.BgVoid,
            $Colors.BgDeep,
            135.0
        )
        $g.FillRectangle($bgBrush, $bgRect)
        $bgBrush.Dispose()

        # Grille cyber
        $gridPen = New-Object System.Drawing.Pen(
            [System.Drawing.Color]::FromArgb(20, 0, 240, 255),
            1
        )
        for ($x = 0; $x -lt $Width; $x += 40) {
            $g.DrawLine($gridPen, $x, 0, $x, $Height)
        }
        for ($y = 0; $y -lt $Height; $y += 40) {
            $g.DrawLine($gridPen, 0, $y, $Width, $y)
        }
        $gridPen.Dispose()

        # Halos radiaux
        $haloBrush1 = New-Object System.Drawing.Drawing2D.PathGradientBrush(
            (New-RoundedRectPath -X 0 -Y 0 -Width ($Width * 0.4) -Height ($Height * 0.4) -Radius 100)
        )
        # (simplifié)

        # Logo à gauche
        $logoSize = 400
        $logo = New-NexusLogo -Size $logoSize -AddGlow $true
        $logoX = [int](($Width - $logoSize) / 8)
        $logoY = [int](($Height - $logoSize) / 2)
        $g.DrawImage($logo, $logoX, $logoY, $logoSize, $logoSize)
        $logo.Dispose()

        # Texte : NexusDL
        $titleFont = $null
        foreach ($name in @("Orbitron", "Bahnschrift", "Segoe UI Black", "Arial Black")) {
            try {
                $titleFont = New-Object System.Drawing.Font($name, 96, [System.Drawing.FontStyle]::Bold)
                break
            } catch { continue }
        }
        if ($null -eq $titleFont) {
            $titleFont = New-Object System.Drawing.Font("Arial", 96, [System.Drawing.FontStyle]::Bold)
        }

        $textX = $logoX + $logoSize + 60
        $titleY = [int]($Height / 2 - 120)

        # Effet glow sur le texte
        for ($i = 6; $i -ge 1; $i--) {
            $glowAlpha = [int](255 * (1 - $i / 6.0) * 0.3)
            $glowColor = [System.Drawing.Color]::FromArgb($glowAlpha, 0, 240, 255)
            $glowBrush = New-Object System.Drawing.SolidBrush($glowColor)
            $g.DrawString("NexusDL", $titleFont, $glowBrush, ($textX - $i), ($titleY - $i))
            $g.DrawString("NexusDL", $titleFont, $glowBrush, ($textX + $i), ($titleY + $i))
            $glowBrush.Dispose()
        }

        # Texte principal avec dégradé
        $titleBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            (New-Object System.Drawing.RectangleF($textX, $titleY, 500, 120)),
            $Colors.NeonCyan,
            $Colors.NeonMagenta,
            45.0
        )
        $g.DrawString("NexusDL", $titleFont, $titleBrush, $textX, $titleY)
        $titleBrush.Dispose()
        $titleFont.Dispose()

        # Tagline
        $taglineFont = $null
        foreach ($name in @("Rajdhani", "Bahnschrift", "Segoe UI", "Arial")) {
            try {
                $taglineFont = New-Object System.Drawing.Font($name, 32, [System.Drawing.FontStyle]::Regular)
                break
            } catch { continue }
        }
        if ($null -eq $taglineFont) {
            $taglineFont = New-Object System.Drawing.Font("Arial", 32, [System.Drawing.FontStyle]::Regular)
        }

        $taglineBrush = New-Object System.Drawing.SolidBrush($Colors.TextMuted)
        $g.DrawString($Tagline, $taglineFont, $taglineBrush, $textX, ($titleY + 130))
        $taglineBrush.Dispose()
        $taglineFont.Dispose()

        # Badge "60+ sources"
        $badgeFont = New-Object System.Drawing.Font("Arial", 20, [System.Drawing.FontStyle]::Bold)
        $badgeText = "60+ SOURCES  ·  3 INTERFACES  ·  OPEN SOURCE"
        $badgeBrush = New-Object System.Drawing.SolidBrush($Colors.NeonCyan)
        $g.DrawString($badgeText, $badgeFont, $badgeBrush, $textX, ($titleY + 200))
        $badgeBrush.Dispose()
        $badgeFont.Dispose()

        # Barre néon en bas
        $barHeight = 6
        $barRect = New-Object System.Drawing.RectangleF(0, ($Height - $barHeight), $Width, $barHeight)
        $barBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            $barRect,
            $Colors.NeonCyan,
            $Colors.NeonMagenta,
            0.0
        )
        $g.FillRectangle($barBrush, $barRect)
        $barBrush.Dispose()

    } finally {
        $g.Dispose()
    }

    return $bitmap
}

# ==============================================================================
#  GÉNÉRATION DU FAVICON (.ICO)
# ==============================================================================

function New-FaviconIco {
    <#
    .SYNOPSIS
    Génère un favicon.ico multi-tailles.
    #>
    param(
        [string]$OutputPath,
        [int]$MasterSize = 256
    )

    # Génère les bitmaps aux tailles standards
    $sizes = @(16, 32, 48, 64, 128, 256)
    $bitmaps = @()

    foreach ($size in $sizes) {
        $bmp = New-NexusLogo -Size $size -AddGlow ($size -ge 48)
        $bitmaps += ,@($size, $bmp)
    }

    # Crée le fichier ICO en écrivant manuellement l'en-tête
    # Format ICO : header (6 bytes) + entrées (16 bytes) + données PNG

    $ms = New-Object System.IO.MemoryStream
    $writer = New-Object System.IO.BinaryWriter($ms)

    # --- ICONDIR header ---
    $writer.Write([UInt16]0)          # reserved
    $writer.Write([UInt16]1)          # type (1 = ICO)
    $writer.Write([UInt16]$sizes.Count)  # count

    # --- Prépare les PNG en mémoire ---
    $pngStreams = @()
    foreach ($entry in $bitmaps) {
        $size = $entry[0]
        $bmp = $entry[1]

        $pngStream = New-Object System.IO.MemoryStream
        $bmp.Save($pngStream, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngBytes = $pngStream.ToArray()
        $pngStreams += ,@($size, $pngBytes, $bmp)

        $pngStream.Dispose()
    }

    # Calcule l'offset des données
    $dataOffset = 6 + (16 * $sizes.Count)

    # --- ICONDIRENTRY pour chaque image ---
    foreach ($entry in $pngStreams) {
        $size = $entry[0]
        $pngBytes = $entry[1]

        $width = if ($size -ge 256) { 0 } else { $size }
        $height = if ($size -ge 256) { 0 } else { $size }

        $writer.Write([Byte]$width)         # width
        $writer.Write([Byte]$height)        # height
        $writer.Write([Byte]0)              # color palette
        $writer.Write([Byte]0)              # reserved
        $writer.Write([UInt16]1)            # color planes
        $writer.Write([UInt16]32)           # bpp
        $writer.Write([UInt32]$pngBytes.Length)  # size
        $writer.Write([UInt32]$dataOffset)       # offset

        $dataOffset += $pngBytes.Length
    }

    # --- Écrit les données PNG ---
    foreach ($entry in $pngStreams) {
        $pngBytes = $entry[1]
        $writer.Write($pngBytes)
    }

    # --- Sauvegarde ---
    $writer.Flush()
    [System.IO.File]::WriteAllBytes($OutputPath, $ms.ToArray())

    # Cleanup
    $writer.Dispose()
    $ms.Dispose()

    foreach ($entry in $pngStreams) {
        $entry[2].Dispose()
    }
}

# ==============================================================================
#  GÉNÉRATION DES SCREENSHOTS PLACEHOLDER
# ==============================================================================

function New-PlaceholderScreenshot {
    param(
        [int]$Width,
        [int]$Height,
        [string]$Title,
        [string]$Subtitle = ""
    )

    $bitmap = New-Object System.Drawing.Bitmap($Width, $Height)
    $g = [System.Drawing.Graphics]::FromImage($bitmap)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    try {
        # Fond
        $g.Clear($Colors.BgDeep)

        # Grille cyber
        $gridPen = New-Object System.Drawing.Pen(
            [System.Drawing.Color]::FromArgb(15, 0, 240, 255), 1
        )
        for ($x = 0; $x -lt $Width; $x += 50) {
            $g.DrawLine($gridPen, $x, 0, $x, $Height)
        }
        for ($y = 0; $y -lt $Height; $y += 50) {
            $g.DrawLine($gridPen, 0, $y, $Width, $y)
        }
        $gridPen.Dispose()

        # Barre supérieure
        $topBarHeight = 60
        $topBrush = New-Object System.Drawing.SolidBrush($Colors.BgRaised)
        $g.FillRectangle($topBrush, 0, 0, $Width, $topBarHeight)
        $topBrush.Dispose()

        # Logo mini dans la barre
        $miniLogoSize = 40
        $miniLogo = New-NexusLogo -Size $miniLogoSize -AddGlow $false
        $g.DrawImage($miniLogo, 20, 10, $miniLogoSize, $miniLogoSize)
        $miniLogo.Dispose()

        # Titre dans la barre
        $barFont = New-Object System.Drawing.Font("Segoe UI", 18, [System.Drawing.FontStyle]::Bold)
        $barBrush = New-Object System.Drawing.SolidBrush($Colors.NeonCyan)
        $g.DrawString("NexusDL", $barFont, $barBrush, 70, 18)
        $barBrush.Dispose()
        $barFont.Dispose()

        # Titre principal centré
        $titleSize = [int]([Math]::Min($Width, $Height) / 12)
        $titleFont = $null
        foreach ($name in @("Orbitron", "Bahnschrift", "Segoe UI", "Arial")) {
            try {
                $titleFont = New-Object System.Drawing.Font($name, $titleSize, [System.Drawing.FontStyle]::Bold)
                break
            } catch { continue }
        }
        if ($null -eq $titleFont) {
            $titleFont = New-Object System.Drawing.Font("Arial", $titleSize, [System.Drawing.FontStyle]::Bold)
        }

        $sf = New-Object System.Drawing.StringFormat
        $sf.Alignment = [System.Drawing.StringAlignment]::Center
        $sf.LineAlignment = [System.Drawing.StringAlignment]::Center

        $textRect = New-Object System.Drawing.RectangleF(0, ($Height / 2 - 60), $Width, 120)

        # Glow
        for ($i = 4; $i -ge 1; $i--) {
            $glowColor = [System.Drawing.Color]::FromArgb(80, 0, 240, 255)
            $glowBrush = New-Object System.Drawing.SolidBrush($glowColor)
            $offsetRect = New-Object System.Drawing.RectangleF(0, ($Height / 2 - 60 - $i), $Width, 120)
            $g.DrawString($Title, $titleFont, $glowBrush, $offsetRect, $sf)
            $glowBrush.Dispose()
        }

        $titleBrush = New-Object System.Drawing.SolidBrush($Colors.TextLight)
        $g.DrawString($Title, $titleFont, $titleBrush, $textRect, $sf)
        $titleBrush.Dispose()
        $titleFont.Dispose()

        # Sous-titre
        if ($Subtitle) {
            $subFont = New-Object System.Drawing.Font("Segoe UI", ($titleSize / 2), [System.Drawing.FontStyle]::Regular)
            $subBrush = New-Object System.Drawing.SolidBrush($Colors.TextMuted)
            $subRect = New-Object System.Drawing.RectangleF(0, ($Height / 2 + 60), $Width, 60)
            $g.DrawString($Subtitle, $subFont, $subBrush, $subRect, $sf)
            $subBrush.Dispose()
            $subFont.Dispose()
        }

        $sf.Dispose()

    } finally {
        $g.Dispose()
    }

    return $bitmap
}

# ==============================================================================
#  FONCTION DE SAUVEGARDE
# ==============================================================================

function Save-Icon {
    param(
        [System.Drawing.Bitmap]$Bitmap,
        [string]$Path,
        [switch]$Confirm
    )

    if ((Test-Path $Path) -and -not $Force) {
        $response = Read-Host "  ⚠ '$Path' existe déjà. Écraser ? (o/N)"
        if ($response -notmatch '^[oOyY]') {
            Write-Error-Custom "Ignoré : $Path"
            return $false
        }
    }

    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }

    $Bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    return $true
}

# ==============================================================================
#  SCRIPT PRINCIPAL
# ==============================================================================

function Main {
    $scriptStart = Get-Date

    Write-Banner "🌌 NexusDL — Générateur d'icônes et images"
    Write-Host "  Dossier de sortie : " -NoNewline -ForegroundColor Gray
    Write-Host $OutputDir -ForegroundColor Cyan
    Write-Host ""

    # --- Résolution du chemin ---
    $rootDir = Resolve-Path (Join-Path $PSScriptRoot "..") -ErrorAction SilentlyContinue
    if ($null -eq $rootDir) {
        $rootDir = (Get-Location).Path
    }

    $outputPath = Join-Path $rootDir $OutputDir

    Write-Step "Création des dossiers..."
    $iconsDir = Join-Path $outputPath "icons"
    $screenshotsDir = Join-Path $outputPath "screenshots"

    foreach ($dir in @($outputPath, $iconsDir, $screenshotsDir)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }
    Write-Success "Dossiers créés"

    # ==========================================================================
    #  1. LOGO SOURCE HAUTE RÉSOLUTION
    # ==========================================================================
    Write-Banner "1/6 — Logo source haute résolution"

    Write-Step "Génération du logo maître (1024x1024)..."
    $masterLogo = New-NexusLogo -Size 1024 -AddGlow $true
    $masterPath = Join-Path $iconsDir "nexusdl-1024.png"
    if (Save-Icon -Bitmap $masterLogo -Path $masterPath) {
        Write-Success "nexusdl-1024.png (1024x1024)"
    }
    $masterLogo.Dispose()

    # ==========================================================================
    #  2. ICÔNES PWA STANDARD
    # ==========================================================================
    Write-Banner "2/6 — Icônes PWA standard"

    foreach ($size in $IconSizes) {
        Write-Step "Génération icon-${size}x${size}.png..."
        $logo = New-NexusLogo -Size $size -AddGlow ($size -ge 96)
        $path = Join-Path $iconsDir "icon-${size}x${size}.png"
        if (Save-Icon -Bitmap $logo -Path $path) {
            Write-Success "icon-${size}x${size}.png"
        }
        $logo.Dispose()
    }

    # ==========================================================================
    #  3. ICÔNES MASKABLE (ANDROID ADAPTIVE)
    # ==========================================================================
    Write-Banner "3/6 — Icônes maskable (Android adaptive)"

    foreach ($size in @(192, 512)) {
        Write-Step "Génération icon-maskable-${size}x${size}.png..."
        # Padding important pour le maskable (safe zone de 80%)
        $logo = New-NexusLogo -Size $size -Padding 0.15 -AddGlow $true
        $path = Join-Path $iconsDir "icon-maskable-${size}x${size}.png"
        if (Save-Icon -Bitmap $logo -Path $path) {
            Write-Success "icon-maskable-${size}x${size}.png"
        }
        $logo.Dispose()
    }

    # ==========================================================================
    #  4. ICÔNES SPÉCIALES (MONOCHROME, APPLE, FAVICON)
    # ==========================================================================
    Write-Banner "4/6 — Icônes spéciales"

    # Monochrome
    Write-Step "Génération icon-monochrome-512x512.png..."
    $mono = New-NexusLogo -Size 512 -Monochrome $true -Transparent $true -AddGlow $false
    $monoPath = Join-Path $iconsDir "icon-monochrome-512x512.png"
    if (Save-Icon -Bitmap $mono -Path $monoPath) {
        Write-Success "icon-monochrome-512x512.png"
    }
    $mono.Dispose()

    # Apple Touch Icon
    Write-Step "Génération apple-touch-icon.png (180x180)..."
    $apple = New-NexusLogo -Size 180 -AddGlow $true
    $applePath = Join-Path $iconsDir "apple-touch-icon.png"
    if (Save-Icon -Bitmap $apple -Path $applePath) {
        Write-Success "apple-touch-icon.png"
    }
    $apple.Dispose()

    # Favicons PNG
    foreach ($size in @(16, 32)) {
        Write-Step "Génération favicon-${size}x${size}.png..."
        $favicon = New-NexusLogo -Size $size -AddGlow $false
        $faviconPath = Join-Path $iconsDir "favicon-${size}x${size}.png"
        if (Save-Icon -Bitmap $favicon -Path $faviconPath) {
            Write-Success "favicon-${size}x${size}.png"
        }
        $favicon.Dispose()
    }

    # Favicon ICO multi-tailles
    Write-Step "Génération favicon.ico (16/32/48/64/128/256)..."
    $icoPath = Join-Path $iconsDir "favicon.ico"
    try {
        New-FaviconIco -OutputPath $icoPath -MasterSize 256
        Write-Success "favicon.ico (multi-tailles)"
    } catch {
        Write-Error-Custom "Échec favicon.ico : $_"
    }

    # Safari pinned tab (SVG)
    Write-Step "Génération safari-pinned-tab.svg..."
    $safariSvg = @'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">
  <defs>
    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00f0ff"/>
      <stop offset="100%" stop-color="#ff00e5"/>
    </linearGradient>
  </defs>
  <polygon points="100,30 160,65 160,135 100,170 40,135 40,65"
           fill="none" stroke="url(#g)" stroke-width="6"/>
  <text x="100" y="130" font-family="Arial Black, sans-serif" font-size="80"
        font-weight="900" text-anchor="middle" fill="url(#g)">N</text>
</svg>
'@
    $safariSvgPath = Join-Path $iconsDir "safari-pinned-tab.svg"
    [System.IO.File]::WriteAllText($safariSvgPath, $safariSvg)
    Write-Success "safari-pinned-tab.svg"

    # Manifest SVG
    Write-Step "Copie du logo SVG vectoriel..."
    $logoSvg = @'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="hexGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00f0ff"/>
      <stop offset="50%" stop-color="#8b00ff"/>
      <stop offset="100%" stop-color="#ff00e5"/>
    </linearGradient>
    <radialGradient id="bgGrad" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#0a0e27"/>
      <stop offset="100%" stop-color="#050514"/>
    </radialGradient>
  </defs>
  <circle cx="256" cy="256" r="256" fill="url(#bgGrad)"/>
  <polygon points="256,80 400,160 400,352 256,432 112,352 112,160"
           fill="none" stroke="url(#hexGrad)" stroke-width="14" stroke-linejoin="round"/>
  <text x="256" y="330" font-family="Arial Black, sans-serif" font-size="220"
        font-weight="900" text-anchor="middle" fill="#e6f7ff">N</text>
  <circle cx="256" cy="80" r="16" fill="#00f0ff"/>
  <circle cx="400" cy="160" r="16" fill="#00f0ff"/>
  <circle cx="400" cy="352" r="16" fill="#00f0ff"/>
  <circle cx="256" cy="432" r="16" fill="#00f0ff"/>
  <circle cx="112" cy="352" r="16" fill="#00f0ff"/>
  <circle cx="112" cy="160" r="16" fill="#00f0ff"/>
</svg>
'@
    $logoSvgPath = Join-Path $iconsDir "nexusdl.svg"
    [System.IO.File]::WriteAllText($logoSvgPath, $logoSvg)
    Write-Success "nexusdl.svg"

    # ==========================================================================
    #  5. ICÔNES DE RACCOURCIS ET FICHIERS
    # ==========================================================================
    Write-Banner "5/6 — Icônes de raccourcis et fichiers"

    $shortcuts = @(
        @{ Name = "shortcut-search";    Symbol = "🔍"; Color = $Colors.NeonCyan },
        @{ Name = "shortcut-library";   Symbol = "📚"; Color = $Colors.NeonMagenta },
        @{ Name = "shortcut-downloads"; Symbol = "⬇";  Color = $Colors.NeonGreen },
        @{ Name = "shortcut-settings";  Symbol = "⚙";  Color = $Colors.NeonPurple },
        @{ Name = "shortcut-default";   Symbol = "🌌"; Color = $Colors.NeonCyan },
        @{ Name = "file-cbz";           Symbol = "📦"; Color = $Colors.NeonOrange },
        @{ Name = "widget-downloads";   Symbol = "⬇";  Color = $Colors.NeonCyan }
    )

    foreach ($sc in $shortcuts) {
        $sizes = if ($sc.Name -eq "widget-downloads") { @(512) } else { @(96, 192) }

        foreach ($size in $sizes) {
            Write-Step "Génération $($sc.Name)-${size}x${size}.png..."
            $icon = New-ShortcutIcon -Size $size -Symbol $sc.Symbol -AccentColor $sc.Color
            $iconPath = Join-Path $iconsDir "$($sc.Name)-${size}x${size}.png"
            if (Save-Icon -Bitmap $icon -Path $iconPath) {
                Write-Success "$($sc.Name)-${size}x${size}.png"
            }
            $icon.Dispose()
        }
    }

    # ==========================================================================
    #  6. OG IMAGE ET SCREENSHOTS
    # ==========================================================================
    Write-Banner "6/6 — OG image et screenshots"

    # --- OG image 1200x630 ---
    Write-Step "Génération og-image.png (1200x630)..."
    $og = New-OgImage -Width 1200 -Height 630
    $ogPath = Join-Path $outputPath "og-image.png"
    if (Save-Icon -Bitmap $og -Path $ogPath) {
        Write-Success "og-image.png (1200x630)"
    }
    $og.Dispose()

    # --- Twitter Card 1200x600 ---
    Write-Step "Génération twitter-card.png (1200x600)..."
    $twitter = New-OgImage -Width 1200 -Height 600
    $twitterPath = Join-Path $outputPath "twitter-card.png"
    if (Save-Icon -Bitmap $twitter -Path $twitterPath) {
        Write-Success "twitter-card.png (1200x600)"
    }
    $twitter.Dispose()

    # --- Screenshots placeholder (optionnel) ---
    if (-not $SkipScreenshots) {
        $screenshots = @(
            @{ File = "desktop-home.png";      W = 1920; H = 1080; Title = "🏠 Accueil";      Sub = "Page d'accueil NexusDL" },
            @{ File = "desktop-library.png";   W = 1920; H = 1080; Title = "📚 Bibliothèque"; Sub = "Votre collection locale" },
            @{ File = "desktop-search.png";    W = 1920; H = 1080; Title = "🔍 Recherche";    Sub = "Multi-sites en parallèle" },
            @{ File = "desktop-downloads.png"; W = 1920; H = 1080; Title = "⬇ Téléchargements"; Sub = "Suivi en temps réel" },
            @{ File = "mobile-home.png";       W = 1080; H = 1920; Title = "🏠 Accueil";      Sub = "Version mobile" },
            @{ File = "mobile-downloads.png";  W = 1080; H = 1920; Title = "⬇ Téléchargements"; Sub = "Suivi mobile" },
            @{ File = "widget-downloads.png";  W = 600;  H = 400;  Title = "⬇ Widget";        Sub = "Téléchargements" }
        )

        foreach ($s in $screenshots) {
            Write-Step "Génération screenshots/$($s.File)..."
            $img = New-PlaceholderScreenshot -Width $s.W -Height $s.H -Title $s.Title -Subtitle $s.Sub
            $imgPath = Join-Path $screenshotsDir $s.File
            if (Save-Icon -Bitmap $img -Path $imgPath) {
                Write-Success "screenshots/$($s.File) ($($s.W)x$($s.H))"
            }
            $img.Dispose()
        }
    } else {
        Write-Host "  ⊘ Screenshots ignorés (-SkipScreenshots)" -ForegroundColor DarkYellow
    }

    # ==========================================================================
    #  RÉCAPITULATIF
    # ==========================================================================
    $duration = (Get-Date) - $scriptStart

    Write-Banner "✨ Génération terminée"

    # Compte les fichiers générés
    $iconCount = (Get-ChildItem -Path $iconsDir -File -ErrorAction SilentlyContinue).Count
    $screenshotCount = (Get-ChildItem -Path $screenshotsDir -File -ErrorAction SilentlyContinue).Count
    $rootCount = (Get-ChildItem -Path $outputPath -File -ErrorAction SilentlyContinue).Count

    Write-Host "  📁 Dossier de sortie : " -NoNewline -ForegroundColor Gray
    Write-Host $outputPath -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  🖼️  Icônes générées     : " -NoNewline -ForegroundColor Gray
    Write-Host "$iconCount" -ForegroundColor Green
    Write-Host "  📸 Screenshots          : " -NoNewline -ForegroundColor Gray
    Write-Host "$screenshotCount" -ForegroundColor Green
    Write-Host "  📄 Fichiers racine      : " -NoNewline -ForegroundColor Gray
    Write-Host "$rootCount (og-image.png, twitter-card.png)" -ForegroundColor Green
    Write-Host "  ⏱️  Durée totale         : " -NoNewline -ForegroundColor Gray
    Write-Host ("{0:N2} secondes" -f $duration.TotalSeconds) -ForegroundColor Green
    Write-Host ""

    # Arbre des fichiers générés
    Write-Host "  📂 Structure :" -ForegroundColor DarkGray
    Write-Host "     $outputPath/" -ForegroundColor Cyan
    Write-Host "     ├── manifest.json" -ForegroundColor Gray
    Write-Host "     ├── og-image.png (1200×630)" -ForegroundColor Gray
    Write-Host "     ├── twitter-card.png (1200×600)" -ForegroundColor Gray
    Write-Host "     ├── icons/ ($iconCount fichiers)" -ForegroundColor Gray
    Write-Host "     └── screenshots/ ($screenshotCount fichiers)" -ForegroundColor Gray
    Write-Host ""

    Write-Host "  🎉 " -NoNewline -ForegroundColor Green
    Write-Host "Toutes les icônes sont prêtes pour le déploiement PWA !" -ForegroundColor White
    Write-Host ""
    Write-Host "  💡 Prochaines étapes :" -ForegroundColor DarkCyan
    Write-Host "     1. Vérifier les icônes dans $iconsDir" -ForegroundColor Gray
    Write-Host "     2. Lancer le frontend : pnpm dev" -ForegroundColor Gray
    Write-Host "     3. Tester l'installation PWA (DevTools → Application → Manifest)" -ForegroundColor Gray
    Write-Host ""
}

# ==============================================================================
#  POINT D'ENTRÉE
# ==============================================================================

try {
    Main
    exit 0
} catch {
    Write-Host ""
    Write-Host "  ❌ ERREUR : " -ForegroundColor Red -NoNewline
    Write-Host $_.Exception.Message -ForegroundColor White
    Write-Host ""
    Write-Host "  Stack trace :" -ForegroundColor DarkGray
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}
