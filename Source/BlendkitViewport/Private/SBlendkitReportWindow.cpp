#include "SBlendkitReportWindow.h"

#include "BlendkitBridge.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Docking/TabManager.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformApplicationMisc.h"
#include "HAL/PlatformProcess.h"
#include "Styling/AppStyle.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SMultiLineEditableTextBox.h"
#include "Widgets/SWindow.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "BlendkitReport"

TWeakPtr<SWindow> SBlendkitReportWindow::OpenWindow;

namespace
{
	const FBlendkitReportInfo& Info()
	{
		return UBlendkitBridge::Get()->GetReportInfo();
	}

	TSharedRef<SWidget> ActionButton(const FText& Label, const FText& ToolTip, FOnClicked OnClicked,
		TAttribute<bool> IsEnabled = true)
	{
		return SNew(SButton)
			.Text(Label)
			.ToolTipText(ToolTip)
			.IsEnabled(IsEnabled)
			.OnClicked(OnClicked);
	}
}

void SBlendkitReportWindow::Open()
{
	if (TSharedPtr<SWindow> Existing = OpenWindow.Pin())
	{
		Existing->BringToFront();
		return;
	}

	TSharedRef<SWindow> Window = SNew(SWindow)
		.Title(LOCTEXT("Title", "Blendkit - Report a Bug / Request a Feature"))
		.ClientSize(FVector2D(600.f, 470.f))
		.SupportsMinimize(false)
		.SupportsMaximize(false)
		[
			SNew(SBlendkitReportWindow)
		];
	OpenWindow = Window;

	TSharedPtr<SWindow> Root = FGlobalTabmanager::Get()->GetRootWindow();
	if (Root.IsValid())
	{
		FSlateApplication::Get().AddWindowAsNativeChild(Window, Root.ToSharedRef());
	}
	else
	{
		FSlateApplication::Get().AddWindow(Window);
	}
}

void SBlendkitReportWindow::Construct(const FArguments& InArgs)
{
	ChildSlot
	[
		SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight().Padding(12.f, 12.f, 12.f, 4.f)
		[
			SNew(STextBlock)
			.Text(LOCTEXT("Intro",
				"Found a bug or have an idea? Reports go to the Blendkit for Unreal GitHub issue tracker. "
				"The environment details below are filled in for you; please describe what happened, "
				"how to reproduce it, and attach logs if you can."))
			.AutoWrapText(true)
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(12.f, 8.f, 12.f, 3.f)
		[
			SNew(STextBlock)
			.Text(LOCTEXT("EnvLabel", "Environment information"))
			.Font(FAppStyle::GetFontStyle("BoldFont"))
		]
		+ SVerticalBox::Slot().FillHeight(1.f).Padding(12.f, 0.f)
		[
			SNew(SMultiLineEditableTextBox)
			.IsReadOnly(true)
			.AutoWrapText(true)
			.Text_Lambda([] { return FText::FromString(Info().EnvironmentText); })
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(12.f, 10.f, 12.f, 0.f)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth()
			[
				ActionButton(LOCTEXT("Bug", "Report a Bug"),
					LOCTEXT("BugTip", "Open a GitHub bug report prefilled with the environment information"),
					FOnClicked::CreateLambda([this] { return OpenUrl(Info().BugReportUrl); }))
			]
			+ SHorizontalBox::Slot().AutoWidth().Padding(6.f, 0.f)
			[
				ActionButton(LOCTEXT("Feature", "Request a Feature"),
					LOCTEXT("FeatureTip", "Open a GitHub feature request"),
					FOnClicked::CreateLambda([this] { return OpenUrl(Info().FeatureRequestUrl); }))
			]
			+ SHorizontalBox::Slot().AutoWidth()
			[
				ActionButton(LOCTEXT("Issues", "Browse Existing Issues"),
					LOCTEXT("IssuesTip", "Check whether your problem is already reported"),
					FOnClicked::CreateLambda([this] { return OpenUrl(Info().IssuesUrl); }))
			]
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(12.f, 6.f, 12.f, 0.f)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth()
			[
				ActionButton(LOCTEXT("Copy", "Copy Environment Info"),
					LOCTEXT("CopyTip", "Copy the environment information for a support email or an existing issue"),
					FOnClicked::CreateSP(this, &SBlendkitReportWindow::CopyEnvironment))
			]
			+ SHorizontalBox::Slot().AutoWidth().Padding(6.f, 0.f)
			[
				ActionButton(LOCTEXT("UnrealLogs", "Open Unreal Logs"),
					LOCTEXT("UnrealLogsTip", "Open the project log folder; attach the latest .log file to your report"),
					FOnClicked::CreateLambda([this] { return OpenFolder(Info().UnrealLogDir); }),
					TAttribute<bool>::CreateLambda([] { return !Info().UnrealLogDir.IsEmpty(); }))
			]
			+ SHorizontalBox::Slot().AutoWidth()
			[
				ActionButton(LOCTEXT("ClientLogs", "Open Client Logs"),
					LOCTEXT("ClientLogsTip", "Open the Blendkit client log folder (default.log)"),
					FOnClicked::CreateLambda([this] { return OpenFolder(Info().ClientLogDir); }),
					TAttribute<bool>::CreateLambda([] { return !Info().ClientLogDir.IsEmpty(); }))
			]
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(12.f, 10.f, 12.f, 12.f)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().FillWidth(1.f).VAlign(VAlign_Center)
			[
				SNew(STextBlock)
				.Text_Lambda([this] { return Feedback; })
				.AutoWrapText(true)
			]
			+ SHorizontalBox::Slot().AutoWidth()
			[
				SNew(SButton)
				.Text(LOCTEXT("Close", "Close"))
				.OnClicked(this, &SBlendkitReportWindow::Close)
			]
		]
	];
}

FReply SBlendkitReportWindow::OpenUrl(const FString& Url)
{
	if (Url.IsEmpty())
	{
		return FReply::Handled();
	}
	FString Error;
	FPlatformProcess::LaunchURL(*Url, nullptr, &Error);
	Feedback = Error.IsEmpty()
		? LOCTEXT("Opened", "Opened in your web browser.")
		: FText::Format(LOCTEXT("OpenFailed", "Could not open the browser: {0}"), FText::FromString(Error));
	return FReply::Handled();
}

FReply SBlendkitReportWindow::OpenFolder(const FString& Directory)
{
	if (!Directory.IsEmpty())
	{
		IFileManager::Get().MakeDirectory(*Directory, true);
		FPlatformProcess::ExploreFolder(*Directory);
	}
	return FReply::Handled();
}

FReply SBlendkitReportWindow::CopyEnvironment()
{
	FPlatformApplicationMisc::ClipboardCopy(*Info().EnvironmentText);
	Feedback = LOCTEXT("Copied", "Environment info copied to the clipboard.");
	return FReply::Handled();
}

FReply SBlendkitReportWindow::Close()
{
	if (TSharedPtr<SWindow> Window = OpenWindow.Pin())
	{
		Window->RequestDestroyWindow();
	}
	return FReply::Handled();
}

#undef LOCTEXT_NAMESPACE
