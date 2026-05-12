#!/usr/bin/env node
/**
 * Twin Shadow -- KDP Formatter
 * Generates Amazon KDP-ready .docx from story JSON.
 *
 * Usage:
 *   node kdp_formatter.js story.json output.docx
 *
 * Story JSON shape:
 * {
 *   title: string,
 *   author: string,
 *   chapters: string[],   // each chapter is raw text with "# Chapter Title" as first line
 *   year: string          // optional, defaults to current year
 * }
 */

const {
  Document, Packer, Paragraph, TextRun, PageBreak,
  AlignmentType, HeadingLevel, LevelFormat,
  Header, Footer, PageNumber, NumberFormat,
  SectionType,
} = require('docx');
const fs   = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// KDP 6x9 trim size constants (DXA: 1440 = 1 inch)
// ---------------------------------------------------------------------------
const TRIM_W    = 8640;   // 6 inches
const TRIM_H    = 12960;  // 9 inches
const MARGIN_T  = 1440;   // 1 inch top
const MARGIN_B  = 1440;   // 1 inch bottom
const MARGIN_IN = 1080;   // 0.75 inch inside (gutter)
const MARGIN_OUT = 1080;  // 0.75 inch outside
const BODY_SIZE  = 24;    // 12pt in half-points
const HEAD_SIZE  = 36;    // 18pt
const FONT       = 'Georgia';
const INDENT     = 720;   // 0.5 inch paragraph indent

// ---------------------------------------------------------------------------
// Helper: build a body paragraph
// ---------------------------------------------------------------------------
function bodyPara(text, opts = {}) {
  const { firstInChapter = false, centered = false, bold = false, size = BODY_SIZE, spaceAfter = 0 } = opts;
  return new Paragraph({
    alignment: centered ? AlignmentType.CENTER : AlignmentType.JUSTIFIED,
    spacing: { line: 360, lineRule: 'auto', after: spaceAfter },  // double-spaced for manuscript; KDP accepts
    indent: (!firstInChapter && !centered) ? { firstLine: INDENT } : {},
    children: [
      new TextRun({
        text,
        font:  FONT,
        size,
        bold:  bold || false,
      }),
    ],
  });
}

// ---------------------------------------------------------------------------
// Helper: chapter heading paragraph (new page)
// ---------------------------------------------------------------------------
function chapterHeading(title, includePageBreak = true) {
  const paras = [];

  if (includePageBreak) {
    paras.push(new Paragraph({
      children: [new PageBreak()],
      spacing: { after: 0 },
    }));
    // Blank line after page break
    paras.push(new Paragraph({ children: [new TextRun({ text: '', font: FONT, size: BODY_SIZE })] }));
    paras.push(new Paragraph({ children: [new TextRun({ text: '', font: FONT, size: BODY_SIZE })] }));
    paras.push(new Paragraph({ children: [new TextRun({ text: '', font: FONT, size: BODY_SIZE })] }));
  }

  paras.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 480, after: 480 },
    children: [
      new TextRun({
        text:  title,
        font:  FONT,
        size:  HEAD_SIZE,
        bold:  true,
        allCaps: false,
      }),
    ],
  }));

  return paras;
}

// ---------------------------------------------------------------------------
// Helper: parse chapter text into paragraphs
// ---------------------------------------------------------------------------
function parseChapter(rawText, chapterIndex) {
  const lines     = rawText.split('\n');
  const paras     = [];
  let   firstPara = true;
  let   title     = `Chapter ${chapterIndex + 1}`;

  // Extract title from first line if it starts with #
  let startIdx = 0;
  if (lines[0] && lines[0].startsWith('#')) {
    title    = lines[0].replace(/^#+\s*/, '').trim();
    startIdx = 1;
  }

  // Add heading
  paras.push(...chapterHeading(title, chapterIndex > 0));

  // Parse body
  for (let i = startIdx; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;

    // Scene break
    if (line === '***' || line === '* * *' || line === '---') {
      paras.push(bodyPara('* * *', { centered: true, spaceAfter: 120 }));
      firstPara = true;  // Next para after scene break = no indent
      continue;
    }

    paras.push(bodyPara(line, { firstInChapter: firstPara }));
    firstPara = false;
  }

  return paras;
}

// ---------------------------------------------------------------------------
// Title page
// ---------------------------------------------------------------------------
function buildTitlePage(title, author, subtitle = '') {
  return [
    new Paragraph({ children: [new PageBreak()], spacing: { after: 0 } }),

    // Lots of space before title
    ...[1,2,3,4,5,6,7,8].map(() =>
      new Paragraph({ children: [new TextRun({ text: '', font: FONT, size: BODY_SIZE })] })
    ),

    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 480 },
      children: [new TextRun({ text: title, font: FONT, size: 56, bold: true })],
    }),

    subtitle ? new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 240 },
      children: [new TextRun({ text: subtitle, font: FONT, size: 28, italics: true })],
    }) : null,

    ...[1,2,3,4,5].map(() =>
      new Paragraph({ children: [new TextRun({ text: '', font: FONT, size: BODY_SIZE })] })
    ),

    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 0 },
      children: [new TextRun({ text: author, font: FONT, size: 28 })],
    }),
  ].filter(Boolean);
}

// ---------------------------------------------------------------------------
// Copyright page
// ---------------------------------------------------------------------------
function buildCopyrightPage(title, author, year) {
  const yr = year || new Date().getFullYear().toString();
  return [
    new Paragraph({ children: [new PageBreak()], spacing: { after: 0 } }),

    ...[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15].map(() =>
      new Paragraph({ children: [new TextRun({ text: '', font: FONT, size: BODY_SIZE })] })
    ),

    bodyPara(`Copyright ${yr} by ${author}`, { centered: true }),
    bodyPara('All rights reserved.', { centered: true }),
    bodyPara('', { centered: true }),
    bodyPara(
      'This is a work of fiction. Names, characters, places, and incidents either are the product ' +
      'of the author\'s imagination or are used fictitiously. Any resemblance to actual persons, ' +
      'living or dead, events, or locales is entirely coincidental.',
      { centered: false, firstInChapter: true, size: 20 }
    ),
    bodyPara('', { centered: true }),
    bodyPara('Published independently.', { centered: true, size: 20 }),
  ];
}

// ---------------------------------------------------------------------------
// Section properties helper
// ---------------------------------------------------------------------------
function sectionProps(mirror = false) {
  return {
    page: {
      size: { width: TRIM_W, height: TRIM_H },
      margin: {
        top:    MARGIN_T,
        bottom: MARGIN_B,
        left:   MARGIN_IN,
        right:  MARGIN_OUT,
        gutter: 0,
      },
    },
  };
}

// ---------------------------------------------------------------------------
// Main build function
// ---------------------------------------------------------------------------
async function buildKDP(storyJson, outputPath) {
  const { title, author, chapters, year, subtitle } = storyJson;

  if (!title || !author || !chapters || !chapters.length) {
    throw new Error('story JSON must have title, author, and chapters array');
  }

  // Build all content paragraphs
  const titlePageParas    = buildTitlePage(title, author, subtitle);
  const copyrightParas    = buildCopyrightPage(title, author, year);
  const chapterParas      = chapters.flatMap((ch, i) => parseChapter(ch, i));

  const allChildren = [
    ...titlePageParas,
    ...copyrightParas,
    ...chapterParas,
  ];

  const doc = new Document({
    styles: {
      default: {
        document: {
          run: { font: FONT, size: BODY_SIZE },
        },
      },
    },
    sections: [
      {
        properties: sectionProps(),
        children: allChildren,
      },
    ],
  });

  const buffer = await Packer.toBuffer(doc);
  fs.writeFileSync(outputPath, buffer);
  console.log(`KDP document written: ${outputPath}`);
  console.log(`  Title:    ${title}`);
  console.log(`  Author:   ${author}`);
  console.log(`  Chapters: ${chapters.length}`);
  console.log(`  Size:     ${(buffer.length / 1024).toFixed(1)} KB`);
}

// ---------------------------------------------------------------------------
// CLI entry point
// ---------------------------------------------------------------------------
const args = process.argv.slice(2);
if (args.length < 2) {
  console.error('Usage: node kdp_formatter.js <story.json> <output.docx>');
  process.exit(1);
}

const storyData = JSON.parse(fs.readFileSync(args[0], 'utf8'));
buildKDP(storyData, args[1])
  .then(() => process.exit(0))
  .catch(err => { console.error(err); process.exit(1); });
