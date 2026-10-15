#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"

class SWindow;

/** Bug report / feature request window. Content comes from UBlendkitBridge::GetReportInfo(). */
class SBlendkitReportWindow : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SBlendkitReportWindow) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

	/** Open the window, or bring the existing one to front. */
	static void Open();

private:
	FReply OpenUrl(const FString& Url);
	FReply OpenFolder(const FString& Directory);
	FReply CopyEnvironment();
	FReply Close();

	FText Feedback;
	static TWeakPtr<SWindow> OpenWindow;
};
